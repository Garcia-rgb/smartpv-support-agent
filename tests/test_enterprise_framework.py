import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select

from support_agent.config import Settings, get_settings, validate_runtime_settings
from support_agent.main import app
from support_agent.models import AuditLog, UserAccount
from support_agent.services.auth import hash_password
from support_agent.services.backups import BackupError, restore_backup
from support_agent.services.cache import load_hits_by_id
from support_agent.services.rag import RAGService


async def login(client, db, tmp_path, role="admin"):
    settings = Settings(auth_enabled=True, confirmation_secret="test-secret",
                        database_url=str(db.bind.url), backup_dir=str(tmp_path / "backups"),
                        knowledge_base_dir=str(tmp_path / "sources"))
    app.dependency_overrides[get_settings] = lambda: settings
    user = UserAccount(username=role, role=role, password_hash=hash_password("password123"))
    db.add(user)
    await db.commit()
    result = await client.post("/auth/login", json={"username": role, "password": "password123"})
    assert result.status_code == 200
    return {"X-CSRF-Token": result.json()["csrf_token"]}, settings


async def test_disabled_document_is_removed_from_queries_and_cached_hits(
    client, db_session, tmp_path,
):
    headers, _ = await login(client, db_session, tmp_path)
    rag = RAGService(db_session)
    doc, _, _ = await rag.ingest(
        "故障说明.txt", "text/plain", "逆变器无直流数值先核对告警".encode())
    before = await rag.search("逆变器无直流数值")
    assert before
    changed = await client.patch("/admin/documents/" + doc.id, headers=headers,
                                 json={"enabled": False, "document_version": "V2"})
    assert changed.status_code == 200
    assert await rag.search("逆变器无直流数值") == []
    assert await load_hits_by_id(db_session, [(before[0].chunk.id, 1.0)],
                                 corpus_id=None, include_restricted=False) == []
    listed = (await client.get("/admin/documents")).json()["items"][0]
    assert listed["enabled"] is False and listed["document_version"] == "V2"
    assert await db_session.scalar(select(AuditLog.id).where(
        AuditLog.action == "document_updated"))
    await client.patch("/admin/documents/" + doc.id, headers=headers, json={"enabled": True})
    assert await rag.search("逆变器无直流数值")


async def test_user_cannot_access_operations_even_with_direct_requests(
    client, db_session, tmp_path,
):
    headers, _ = await login(client, db_session, tmp_path, "user")
    for path in ("/admin/overview", "/admin/documents", "/admin/audit", "/admin/backups"):
        assert (await client.get(path)).status_code == 403
    assert (await client.post("/admin/backups", headers=headers)).status_code == 403
    assert (await client.patch("/admin/documents/nonexistent", headers=headers,
                              json={"enabled": False})).status_code == 403


async def test_backup_api_and_restore_preserve_data_and_revoke_old_logins(
    client, db_session, tmp_path,
):
    headers, settings = await login(client, db_session, tmp_path)
    sources = Path(settings.knowledge_base_dir)
    sources.mkdir()
    (sources / "课程.txt").write_text("设备课程原文", encoding="utf-8")
    created = await client.post("/admin/backups", headers=headers)
    assert created.status_code == 201
    name = created.json()["name"]
    assert (await client.get("/admin/backups/" + name)).status_code == 200
    assert (await client.get("/admin/backups/not-a-backup.zip")).status_code == 404
    target = tmp_path / "restored"
    db_path = restore_backup(Path(settings.backup_dir) / name, target)
    assert (target / "knowledge_base/课程.txt").read_text(encoding="utf-8") == "设备课程原文"
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM user_accounts").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM login_sessions").fetchone()[0] == 0
    with pytest.raises(BackupError, match="已存在"):
        restore_backup(Path(settings.backup_dir) / name, target)


def test_production_requires_authentication_and_unique_secret():
    with pytest.raises(ValueError, match="身份验证"):
        validate_runtime_settings(Settings(app_env="production", auth_enabled=False))
    with pytest.raises(ValueError, match="32"):
        validate_runtime_settings(Settings(app_env="production", confirmation_secret="short"))
    validate_runtime_settings(Settings(app_env="production", confirmation_secret="x" * 40))


def test_corrupt_backup_is_rejected_before_destination_is_created(tmp_path):
    archive = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("database.sqlite", b"modified")
        output.writestr("manifest.json", json.dumps({
            "format": 1, "files": {"database.sqlite": "incorrect-checksum"},
        }))
    with pytest.raises(BackupError, match="校验失败"):
        restore_backup(archive, tmp_path / "recovered")
    assert not (tmp_path / "recovered").exists()
