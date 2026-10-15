"""Create a Windows shortcut with the app icon; archive the legacy CMD launcher."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_NAME = "光伏电站技术支持 Agent.cmd"

def desktop_directory() -> Path:
    """读注册表拿桌面路径，能正确处理被 OneDrive 重定向的情况。"""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        ) as key:
            return Path(winreg.QueryValueEx(key, "Desktop")[0])
    except OSError:
        return Path.home() / "Desktop"


def create_shortcut(destination, target, arguments, working_directory, icon):
    subprocess.run([
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(Path(__file__).with_name("create_shortcut.ps1")),
        "-ShortcutPath", str(destination), "-TargetPath", str(target),
        "-TargetArguments", arguments, "-WorkingDirectory", str(working_directory),
        "-IconPath", str(icon),
    ], check=True, timeout=30)
    if not Path(destination).is_file():
        raise RuntimeError("桌面快捷方式未生成")


def main() -> int:
    if sys.platform != "win32":
        print("该脚本只用于 Windows。")
        return 1

    script = ROOT / "scripts" / "start_desktop.py"
    if not script.exists():
        print(f"找不到启动脚本：{script}")
        return 1

    windowed_python = Path(sys.executable).with_name("pythonw.exe")
    interpreter = str(windowed_python) if windowed_python.exists() else sys.executable
    destination = desktop_directory() / LAUNCHER_NAME.replace(".cmd", ".lnk")
    create_shortcut(destination, interpreter, '"' + str(script) + '"', ROOT,
                    ROOT / "static/app-icon.ico")
    legacy = desktop_directory() / LAUNCHER_NAME
    if legacy.exists():
        archive = ROOT / ".desktop/legacy-desktop-launcher.cmd"
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy, archive)
        legacy.unlink()

    print(f"启动器已创建：{destination}")
    print(f"  解释器：{sys.executable}")
    print(f"  项目目录：{ROOT}")
    print("双击即可打开独立桌面客户端。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
