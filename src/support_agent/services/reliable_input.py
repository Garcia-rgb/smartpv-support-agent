"""Bound local concurrent work and replay completed input results safely."""

import asyncio
import hashlib
import re
import weakref

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from ..models import InputReceipt

_locks = weakref.WeakValueDictionary()


def session_lock(owner: str, session: str) -> asyncio.Lock:
    key = (owner, session)
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


async def reliable_input(db, owner, session, key, fingerprint, operation, timeout=120):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{16,64}", key):
        raise HTTPException(400, "发送编号不合法")
    identifier = hashlib.sha256((owner + ":" + key).encode()).hexdigest()
    previous = await db.get(InputReceipt, identifier)
    if previous:
        if previous.fingerprint != fingerprint:
            raise HTTPException(409, "发送内容已变化，请作为新消息发送")
        if previous.state == "completed":
            return previous.result
        raise HTTPException(409, "这条消息正在处理或结果未确认，请先查看历史对话，避免重复发送")
    lock = session_lock(owner, session or "new")
    if lock.locked():
        raise HTTPException(409, "当前会话正在处理另一条消息，请稍后再试")
    async with lock:
        receipt = InputReceipt(id=identifier, fingerprint=fingerprint)
        db.add(receipt)
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(409, "这条消息已在处理，请稍后查看会话") from exc
        try:
            async with asyncio.timeout(timeout):
                result = await operation()
            receipt.state = "completed"
            receipt.result = result.model_dump(mode="json")
            await db.commit()
            return receipt.result
        except BaseException as exc:
            await db.rollback()
            receipt = await db.get(InputReceipt, identifier)
            if receipt:
                receipt.state = "uncertain"
                await db.commit()
            if isinstance(exc, TimeoutError):
                raise HTTPException(
                    504, "处理超时，请先查看历史对话确认是否已保存，再决定是否重新提问"
                ) from exc
            raise
