import asyncio
import json
import sqlite3
import zipfile
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker

from support_agent.config import Settings
from support_agent.schemas import UnifiedInputResponse
from support_agent.services.automatic_backup import automatic_backup
from support_agent.services.backups import create_backup, restore_backup
from support_agent.services.file_lock import file_lock
from support_agent.services.reliable_input import reliable_input
from support_agent.services.restore_drill import restore_drill


def answer():
    from support_agent.schemas import ChatResponse
    return UnifiedInputResponse(kind="chat", chat=ChatResponse(
        session_id="session", message_id="message", answer="已完成"
    ))


async def test_duplicate_completed_request_returns_saved_result(db_session):
    work = AsyncMock(return_value=answer())
    key = "request_1234567890"
    first = await reliable_input(db_session, "alice", "s", key, "same", work)
    second = await reliable_input(db_session, "alice", "s", key, "same", work)
    assert first == second
    assert work.await_count == 1
    with pytest.raises(HTTPException, match="发送内容已变化"):
        await reliable_input(db_session, "alice", "s", key, "changed", work)
    await reliable_input(db_session, "bob", "s", key, "same", work)
    assert work.await_count == 2


async def test_uncertain_execution_does_not_run_again(db_session):
    work = AsyncMock(side_effect=RuntimeError("模拟保存后响应失败"))
    with pytest.raises(RuntimeError):
        await reliable_input(db_session, "alice", "s", "uncertain_12345678", "f", work)
    with pytest.raises(HTTPException, match="结果未确认"):
        await reliable_input(db_session, "alice", "s", "uncertain_12345678", "f", work)
    assert work.await_count == 1


async def test_input_timeout_is_bounded_and_recorded(db_session):
    async def slow():
        await asyncio.sleep(.2)
        return answer()
    with pytest.raises(HTTPException, match="处理超时") as error:
        await reliable_input(db_session, "alice", "s", "timeout_1234567890", "f", slow, .01)
    assert error.value.status_code == 504


async def test_concurrent_turn_in_same_chat_is_rejected(db_session):
    started, finish = asyncio.Event(), asyncio.Event()
    async def work():
        started.set()
        await finish.wait()
        return answer()
    task = asyncio.create_task(reliable_input(
        db_session, "alice", "s", "concurrent_1234561", "f", work
    ))
    await started.wait()
    async with async_sessionmaker(db_session.bind)() as second:
        with pytest.raises(HTTPException, match="另一条消息"):
            await reliable_input(second, "alice", "s", "concurrent_1234562", "f", work)
    finish.set()
    await task


def backup_settings(tmp_path):
    database = tmp_path / "source.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE messages(id INTEGER,content TEXT)")
        db.execute("INSERT INTO messages VALUES(1,'测试对话')")
        db.commit()
    source = tmp_path / "knowledge"
    source.mkdir()
    (source / "example.txt").write_text("测试资料", encoding="utf-8")
    return Settings(_env_file=None, database_url="sqlite:///" + str(database),
                    knowledge_base_dir=str(source), backup_dir=str(tmp_path / "backups"),
                    automatic_backup_keep=1)


def test_automatic_backup_retention_preserves_manual_and_restores(tmp_path):
    settings = backup_settings(tmp_path)
    manual = create_backup(settings)
    first = automatic_backup(settings, force=True)
    second = automatic_backup(settings, force=True)
    assert first["status"] == second["status"] == "ok"
    folder = tmp_path / "backups"
    assert (folder / manual["name"]).exists()
    assert (folder / second["backup"]).exists()
    assert len(list(folder.glob("backup-*.zip"))) == 2
    assert automatic_backup(settings)["status"] == "not_due"
    result = restore_drill(settings, second["backup"])
    assert result["counts"]["messages"] == 1
    assert result["active_database_changed"] is False
    assert (tmp_path / "knowledge/example.txt").read_text("utf-8") == "测试资料"


def test_corrupt_backup_never_overwrites_active_data(tmp_path):
    settings = backup_settings(tmp_path)
    result = create_backup(settings)
    original = tmp_path / "backups" / result["name"]
    corrupt = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(original) as src, zipfile.ZipFile(corrupt, "w") as dst:
        for name in src.namelist():
            dst.writestr(name, b"broken" if name == "database.sqlite" else src.read(name))
    with pytest.raises(ValueError, match="校验失败"):
        restore_backup(corrupt, tmp_path / "recovered")
    assert not (tmp_path / "recovered").exists()


def test_instance_lock_blocks_second_process_entry_and_releases(tmp_path):
    path = tmp_path / "instance.lock"
    with file_lock(path):
        with pytest.raises(OSError), file_lock(path):
            pass
    with file_lock(path):
        pass


async def test_synthetic_answer_acceptance(db_session):
    from pathlib import Path

    from support_agent.services.acceptance import run_acceptance
    suite = json.loads(Path("tests/fixtures/answer_acceptance.json").read_text("utf-8"))
    result = await run_acceptance(db_session, suite["cases"],
                                  Settings(_env_file=None, privacy_routing_enabled=True))
    assert result["passed"] == result["total"], result["results"]
