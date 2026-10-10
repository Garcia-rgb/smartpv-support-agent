"""Local, verified SQLite backups and restoration into a new directory."""

import hashlib
import json
import re
import sqlite3
import tempfile
import uuid
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from .. import __version__
from ..config import Settings

NAME_RE = re.compile(r"backup-[0-9TZ-]+-[a-f0-9]{8}\.zip\Z")


class BackupError(ValueError):
    pass


def database_path(settings: Settings) -> Path:
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise BackupError("网页备份支持本机 SQLite；PostgreSQL 请使用 pg_dump 备份")
    path = Path(url.database).resolve()
    if not path.is_file():
        raise BackupError("本地数据库不存在")
    return path


def backup_path(settings: Settings, name: str) -> Path:
    if not NAME_RE.fullmatch(name):
        raise BackupError("备份名称不合法")
    folder = Path(settings.backup_dir).resolve()
    path = folder / name
    if path.is_symlink() or not path.is_file():
        raise BackupError("备份不存在")
    return path


def list_backups(settings: Settings) -> list[dict]:
    folder = Path(settings.backup_dir).resolve()
    if not folder.exists():
        return []
    return [{"name": p.name, "bytes": p.stat().st_size,
             "created_at": datetime.fromtimestamp(p.stat().st_mtime, UTC).isoformat()}
            for p in sorted(folder.glob("backup-*.zip"), reverse=True)
            if NAME_RE.fullmatch(p.name) and p.is_file() and not p.is_symlink()]


def create_backup(settings: Settings, *, kind: str = "manual") -> dict:
    database = database_path(settings)
    folder = Path(settings.backup_dir).resolve()
    source = Path(settings.knowledge_base_dir).resolve()
    # Never recurse into the backup output if it is configured inside the sources.
    if folder == source or folder.is_relative_to(source):
        raise BackupError("备份目录不能位于资料库内")
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    name = f"backup-{stamp}-{uuid.uuid4().hex[:8]}.zip"
    target = folder / name
    partial = target.with_suffix(".partial")
    manifest = {"format": 1, "version": __version__, "created_at": stamp,
                "kind": kind, "files": {}}
    try:
        with tempfile.TemporaryDirectory(dir=folder) as temp:
            snapshot = Path(temp) / "database.sqlite"
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as src:
                with closing(sqlite3.connect(snapshot)) as dst:
                    src.backup(dst)
                    if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise BackupError("数据库完整性检查失败")
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
                paths = [(snapshot, "database.sqlite")]
                if source.is_dir():
                    paths.extend((p, "knowledge_base/" + p.relative_to(source).as_posix())
                                 for p in sorted(source.rglob("*"))
                                 if p.is_file() and not p.is_symlink()
                                 and p.resolve().is_relative_to(source))
                for path, entry in paths:
                    digest = hashlib.sha256()
                    with path.open("rb") as data, archive.open(entry, "w") as output:
                        while block := data.read(1024 * 1024):
                            digest.update(block)
                            output.write(block)
                    manifest["files"][entry] = digest.hexdigest()
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        partial.replace(target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return {"name": name, "bytes": target.stat().st_size, "created_at": stamp,
            "files": len(manifest["files"])}


def restore_backup(archive_path: Path, destination: Path) -> Path:
    """Restore only to a new folder; never overwrite a live database or data."""
    destination = destination.resolve()
    if destination.exists():
        raise BackupError("恢复目录已存在，请指定新的空目录")
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        entries = manifest.get("files", {})
        if manifest.get("format") != 1 or "database.sqlite" not in entries:
            raise BackupError("备份格式不正确")
        if len(entries) > 10000 or len(archive.namelist()) != len(set(archive.namelist())):
            raise BackupError("备份文件列表不合法")
        for entry in entries:
            if entry != "database.sqlite" and not entry.startswith("knowledge_base/"):
                raise BackupError("备份包含未知文件")
            path = destination / entry
            if "\\" in entry or not path.resolve().is_relative_to(destination):
                raise BackupError("备份包含越界路径")
        # Verify every byte before creating the destination folder.
        for entry, expected in entries.items():
            digest = hashlib.sha256()
            with archive.open(entry) as data:
                while block := data.read(1024 * 1024):
                    digest.update(block)
            if digest.hexdigest() != expected:
                raise BackupError("备份校验失败：" + entry)
        destination.mkdir(parents=True)
        for entry in entries:
            path = destination / entry
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as data, path.open("xb") as output:
                while block := data.read(1024 * 1024):
                    output.write(block)
    recovered = destination / "database.sqlite"
    with closing(sqlite3.connect(recovered)) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BackupError("恢复后的数据库完整性检查失败")
        # Restored snapshots must not reactivate old browser login sessions.
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='login_sessions'").fetchone():
            db.execute("DELETE FROM login_sessions")
            db.commit()
    return recovered


def archive_source(settings: Settings, filename: str, data: bytes) -> None:
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".txt", ".md", ".markdown", ".text"}:
        suffix = ".bin"
    folder = Path(settings.knowledge_base_dir).resolve() / "imports"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (hashlib.sha256(data).hexdigest() + suffix)
    if not target.exists():
        temporary = folder / (uuid.uuid4().hex + ".partial")
        try:
            temporary.write_bytes(data)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
