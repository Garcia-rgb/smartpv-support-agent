"""Install verified local releases; preserve data and switch versions atomically."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / ".localdeps")]

from support_agent.services.file_lock import file_lock  # noqa: E402


def verify(package: Path):
    manifest = json.loads((package / "manifest.json").read_text("utf-8"))
    if (manifest.get("format") != 1 or manifest.get("schema") != 1
            or not re.fullmatch(r"\d+\.\d+\.\d+", str(manifest.get("version", "")))
            or not isinstance(manifest.get("files"), dict)):
        raise ValueError("安装包格式不正确")
    allowed = {"src", "static", "scripts", ".runtime", ".localdeps", "models"}
    for name, expected in manifest["files"].items():
        parts = PurePosixPath(name).parts
        if (not parts or ".." in parts or "\\" in name or ":" in name
                or PurePosixPath(name).is_absolute()):
            raise ValueError("安装包包含越界路径")
        if parts[0] not in allowed and name not in {"LICENSE", ".env.example", "README.md"}:
            raise ValueError("安装包包含用户数据或未知文件")
        path = package / name
        if path.is_symlink() or not path.resolve().is_relative_to(package.resolve()):
            raise ValueError("安装包路径不正确")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        if digest.hexdigest() != expected:
            raise ValueError("安装包校验失败：" + name)
    for required in (".runtime/python.exe", ".runtime/pythonw.exe", "scripts/start_desktop.py",
                     "scripts/release_probe.py", "static/index.html"):
        if required not in manifest["files"]:
            raise ValueError("安装包缺少运行文件：" + required)
    return manifest


def environment(release: Path, data: Path):
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env["SUPPORT_AGENT_DATA_DIR"] = str(data)
    env["PYTHONPATH"] = str(release / "src") + os.pathsep + str(release / ".localdeps")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def save_state(target, state):
    temp = target / "install-state.partial"
    temp.write_text(json.dumps(state), encoding="utf-8")
    temp.replace(target / "install-state.json")


def install(package: Path, target: Path, *, shortcuts=True):
    manifest = verify(package)
    target = target.resolve()
    data = target / "data"
    data.mkdir(parents=True, exist_ok=True)
    with file_lock(data / ".desktop" / "instance.lock"):
        state_path = target / "install-state.json"
        old = json.loads(state_path.read_text("utf-8")) if state_path.exists() else {}
        if old.get("current") == manifest["version"]:
            if verify(target / "releases" / manifest["version"])["files"] != manifest["files"]:
                raise ValueError("同版本程序内容不一致，请使用新的版本号")
            return {"status": "already_installed", "version": manifest["version"]}
        if old.get("current"):
            if not re.fullmatch(r"\d+\.\d+\.\d+", old["current"]):
                raise ValueError("已安装版本记录不合法")
            previous = target / "releases" / old["current"]
            subprocess.run([str(previous / ".runtime/python.exe"),
                            str(previous / "scripts/backup_project.py")],
                           env=environment(previous, data), cwd=previous, check=True, timeout=600)
        release = target / "releases" / manifest["version"]
        if release.exists():
            if verify(release)["files"] != manifest["files"]:
                raise ValueError("同版本程序内容不一致，请使用新的版本号")
        else:
            stage = target / "releases" / ("staging-" + uuid.uuid4().hex)
            stage.mkdir(parents=True)
            for name in manifest["files"]:
                destination = stage / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(package / name, destination)
            shutil.copy2(package / "manifest.json", stage / "manifest.json")
            stage.rename(release)
        config = data / ".env"
        if not config.exists():
            import secrets
            config.write_text(
                "AUTH_ENABLED=true\nAUTOMATIC_BACKUP_ENABLED=true\n"
                "CONFIRMATION_SECRET=" + secrets.token_urlsafe(36) + "\n"
                "ALLOW_REMOTE_LLM=false\nPRIVACY_ROUTING_ENABLED=true\n",
                encoding="utf-8",
            )
        env = environment(release, data)
        subprocess.run([str(release / ".runtime/python.exe"),
                        str(release / "scripts/release_probe.py"), "--expected-version",
                        manifest["version"]],
                       env=env, cwd=release, check=True, timeout=300)
        subprocess.run([str(release / ".runtime/python.exe"),
                        str(release / "scripts/create_admin.py"), "--generate"],
                       env=env, cwd=release, check=True, timeout=120)
        save_state(target, {"current": manifest["version"], "previous": old.get("current")})
        launcher = target / "Launch.ps1"
        launcher.write_text(
            "$ErrorActionPreference='Stop'\n$root=$PSScriptRoot\n"
            "$state=Get-Content -LiteralPath (Join-Path $root 'install-state.json') "
            "| ConvertFrom-Json\n"
            "if($state.current -notmatch '^\\d+\\.\\d+\\.\\d+$'){throw 'Invalid release'}\n"
            "$release=Join-Path $root ('releases\\'+$state.current)\n"
            "$env:SUPPORT_AGENT_DATA_DIR=Join-Path $root 'data'\n"
            "$env:PYTHONPATH=$release+'\\src;'+$release+'\\.localdeps'\n"
            "$env:PYTHONNOUSERSITE='1'\nRemove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue\n"
            "Start-Process -FilePath (Join-Path $release '.runtime\\pythonw.exe') "
            "-ArgumentList ('\"'+(Join-Path $release 'scripts\\start_desktop.py')+'\"') "
            "-WorkingDirectory $release -WindowStyle Hidden\n", encoding="utf-8-sig",
        )
        launch_cmd = (
            '@echo off\r\npowershell -NoProfile -ExecutionPolicy Bypass -File "'
            + str(launcher) + '"\r\n'
        )
        (target / "Launch.cmd").write_text(launch_cmd, encoding="utf-8")
        (target / "Rollback.cmd").write_text(
            '@echo off\r\n"' + str(release / ".runtime/python.exe") + '" "'
            + str(release / "scripts/manage_install.py") + '" rollback --target "'
            + str(target) + '"\r\npause\r\n', encoding="utf-8",
        )
        if shortcuts:
            from create_desktop_launcher import desktop_directory
            shortcut = desktop_directory() / "光伏技术支持客户端.cmd"
            shortcut.write_text(launch_cmd, encoding="utf-8")
    return {"status": "installed", "version": manifest["version"], "data": str(data)}


def rollback(target: Path):
    target = target.resolve()
    with file_lock(target / "data/.desktop/instance.lock"):
        state = json.loads((target / "install-state.json").read_text("utf-8"))
        version = state.get("previous")
        if not version or not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ValueError("没有可回退的旧版本")
        verify(target / "releases" / version)
        save_state(target, {"current": version, "previous": state["current"]})
    return {"status": "rolled_back", "version": version, "data_changed": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["install", "rollback"])
    parser.add_argument("--package", type=Path, default=ROOT)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--no-shortcuts", action="store_true")
    args = parser.parse_args()
    try:
        result = install(args.package.resolve(), args.target, shortcuts=not args.no_shortcuts) if (
            args.mode == "install"
        ) else rollback(args.target)
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print("安装/升级失败，请先关闭客户端。旧版本和用户数据保留。", str(exc))
        raise SystemExit(1) from exc
