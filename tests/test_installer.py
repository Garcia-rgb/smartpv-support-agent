import hashlib
import json
import runpy
from pathlib import Path

import pytest

INSTALLER = runpy.run_path(str(Path(__file__).parents[1] / "scripts/manage_install.py"))


def package_at(folder, version):
    files = {}
    for name in (".runtime/python.exe", ".runtime/pythonw.exe", "scripts/start_desktop.py",
                 "scripts/release_probe.py", "static/index.html"):
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic test fixture")
        files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (folder / "manifest.json").write_text(json.dumps(
        {"format": 1, "version": version, "schema": 1, "files": files}
    ), encoding="utf-8")
    return folder


def test_install_rejects_corruption_and_traversal(tmp_path):
    package = package_at(tmp_path / "package", "1.0.0")
    (package / "static/index.html").write_text("corrupted")
    with pytest.raises(ValueError, match="校验失败"):
        INSTALLER["verify"](package)
    manifest = json.loads((package / "manifest.json").read_text())
    manifest["files"] = {"../private.db": "invalid"}
    (package / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="越界"):
        INSTALLER["verify"](package)


def test_upgrade_and_rollback_preserve_data_and_configuration(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(INSTALLER["subprocess"], "run", lambda args, **kw: calls.append(args))
    target = tmp_path / "installed"
    INSTALLER["install"](package_at(tmp_path / "first", "1.0.0"), target, shortcuts=False)
    database = target / "data/support_agent.db"
    database.write_bytes(b"existing accounts and conversations")
    config = (target / "data/.env").read_bytes()
    INSTALLER["install"](package_at(tmp_path / "second", "1.1.0"), target, shortcuts=False)
    assert any("backup_project.py" in str(args) for args in calls)
    assert INSTALLER["rollback"](target)["version"] == "1.0.0"
    assert database.read_bytes() == b"existing accounts and conversations"
    assert (target / "data/.env").read_bytes() == config
    (target / "releases/1.0.0/static/index.html").write_text("corrupted")
    with pytest.raises(ValueError, match="校验失败"):
        INSTALLER["install"](tmp_path / "first", target, shortcuts=False)


def test_failed_probe_keeps_previous_active_version(tmp_path, monkeypatch):
    monkeypatch.setattr(INSTALLER["subprocess"], "run", lambda *a, **kw: None)
    target = tmp_path / "installed"
    INSTALLER["install"](package_at(tmp_path / "first", "1.0.0"), target, shortcuts=False)

    def fail_probe(args, **kwargs):
        if "release_probe.py" in str(args):
            raise INSTALLER["subprocess"].CalledProcessError(1, args)

    monkeypatch.setattr(INSTALLER["subprocess"], "run", fail_probe)
    with pytest.raises(INSTALLER["subprocess"].CalledProcessError):
        INSTALLER["install"](package_at(tmp_path / "second", "1.1.0"), target, shortcuts=False)
    assert json.loads((target / "install-state.json").read_text())["current"] == "1.0.0"
