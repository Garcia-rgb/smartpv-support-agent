"""Admin operations: document lifecycle, audit, runtime overview, local backups."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import __version__
from .config import Settings, get_settings
from .db import get_db
from .models import (
    AuditLog,
    Conversation,
    DocumentChunk,
    Feedback,
    Message,
    SourceDocument,
    Ticket,
    UserAccount,
)
from .services.auth import admin_user
from .services.automatic_backup import automatic_backup, backup_state
from .services.backups import BackupError, backup_path, create_backup, list_backups
from .services.cache import get_retrieval_cache
from .services.restore_drill import restore_drill

router = APIRouter(prefix="/admin", tags=["管理中心"])
DB = Annotated[AsyncSession, Depends(get_db)]
Config = Annotated[Settings, Depends(get_settings)]


async def require_admin(user: Annotated[UserAccount | None, Depends(admin_user)]) -> UserAccount:
    if user is None:
        raise HTTPException(403, "需要管理员权限")
    return user


Admin = Annotated[UserAccount, Depends(require_admin)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


class DocumentUpdate(BaseModel):
    enabled: bool | None = None
    document_version: str | None = Field(default=None, max_length=80)
    visibility: Literal["public", "private"] | None = None


@router.get("/overview")
async def overview(db: DB, settings: Config, admin: Admin, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    counts = {}
    for name, model in (("documents", SourceDocument), ("chunks", DocumentChunk),
                        ("users", UserAccount), ("conversations", Conversation),
                        ("messages", Message), ("tickets", Ticket)):
        counts[name] = await db.scalar(select(func.count()).select_from(model)) or 0
    counts["negative_feedback"] = await db.scalar(
        select(func.count()).select_from(Feedback).where(Feedback.rating < 0)
    ) or 0
    return {"version": __version__, "counts": counts, "auth_enabled": settings.auth_enabled,
            "model_enabled": settings.llm_enabled, "embedding_backend": settings.embedding_backend,
            "backup_supported": settings.database_url.startswith("sqlite"),
            "latest_backup": (await run_in_threadpool(list_backups, settings))[:1]}


@router.get("/documents")
async def documents(db: DB, admin: Admin, limit: Limit = 25, offset: Offset = 0) -> dict:
    total = await db.scalar(select(func.count()).select_from(SourceDocument)) or 0
    docs = (await db.scalars(select(SourceDocument).order_by(
        SourceDocument.created_at.desc(), SourceDocument.id).offset(offset).limit(limit))).all()
    grouped = {doc.id: [] for doc in docs}
    if docs:
        for document_id, metadata in (await db.execute(
                select(DocumentChunk.document_id, DocumentChunk.chunk_metadata).where(
                    DocumentChunk.document_id.in_(grouped)))).all():
            grouped[document_id].append(metadata or {})
    items = []
    for doc in docs:
        metadata = grouped[doc.id]
        scopes = {m.get("visibility", "private") for m in metadata}
        versions = sorted({str(m.get("document_version", "")) for m in metadata})
        items.append({"id": doc.id, "filename": doc.filename, "chunks": len(metadata),
                      "created_at": doc.created_at, "enabled": any(
                          m.get("enabled") is not False for m in metadata),
                      "visibility": next(iter(scopes)) if len(scopes) == 1 else "mixed",
                      "document_version": versions[0] if len(versions) == 1 else ""})
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.patch("/documents/{document_id}")
async def update_document(document_id: str, body: DocumentUpdate, db: DB,
                          settings: Config, admin: Admin) -> dict:
    if not body.model_fields_set:
        raise HTTPException(422, "请提供需要修改的字段")
    doc = await db.get(SourceDocument, document_id)
    if doc is None:
        raise HTTPException(404, "资料不存在")
    chunks = (await db.scalars(select(DocumentChunk).where(
        DocumentChunk.document_id == document_id))).all()
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(422, "修改内容不能为空")
    for chunk in chunks:
        chunk.chunk_metadata = {**(chunk.chunk_metadata or {}), **changes}
    db.add(AuditLog(actor=admin.id, action="document_updated", resource=document_id,
                    detail=changes))
    await db.commit()
    await get_retrieval_cache(settings).invalidate()
    return {"id": document_id, "updated_chunks": len(chunks), **changes}


@router.get("/audit")
async def audit(db: DB, admin: Admin, limit: Limit = 25, offset: Offset = 0) -> dict:
    total = await db.scalar(select(func.count()).select_from(AuditLog)) or 0
    rows = (await db.execute(select(AuditLog, UserAccount.username).outerjoin(
        UserAccount, AuditLog.actor == UserAccount.id).order_by(
        AuditLog.created_at.desc(), AuditLog.id).offset(offset).limit(limit))).all()
    # Legacy tool audit details can contain prompts; do not expose their payloads here.
    return {"items": [{"id": log.id, "actor": username or log.actor,
                       "action": log.action, "resource": log.resource,
                       "created_at": log.created_at} for log, username in rows], "total": total}


@router.get("/backups")
async def backups(settings: Config, admin: Admin) -> dict:
    return {"items": await run_in_threadpool(list_backups, settings),
            "automatic": {"enabled": settings.automatic_backup_enabled,
                          "interval_hours": settings.automatic_backup_interval_hours,
                          "keep": settings.automatic_backup_keep,
                          **await run_in_threadpool(backup_state, settings)}}


@router.post("/backups/automatic")
async def run_automatic_backup(settings: Config, admin: Admin) -> dict:
    return await run_in_threadpool(automatic_backup, settings, force=True)


@router.post("/backups", status_code=201)
async def backup(db: DB, settings: Config, admin: Admin) -> dict:
    try:
        result = await run_in_threadpool(create_backup, settings)
    except BackupError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.add(AuditLog(actor=admin.id, action="backup_created", resource=result["name"],
                    detail={"files": result["files"]}))
    await db.commit()
    return result


@router.get("/backups/{name}")
async def download_backup(name: str, settings: Config, admin: Admin) -> FileResponse:
    try:
        path = backup_path(settings, name)
    except BackupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, media_type="application/zip", filename=name,
                        headers={"Cache-Control": "no-store"})


@router.post("/backups/{name}/verify")
async def verify_backup(name: str, db: DB, settings: Config, admin: Admin) -> dict:
    try:
        result = await run_in_threadpool(restore_drill, settings, name)
    except (BackupError, OSError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    db.add(AuditLog(actor=admin.id, action="backup_restore_verified", resource=name,
                    detail={"counts": result["counts"]}))
    await db.commit()
    return result
