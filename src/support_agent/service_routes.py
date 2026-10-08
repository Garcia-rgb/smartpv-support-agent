"""User-owned service workflows and explicit administrator case publication."""

import hashlib
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .db import get_db
from .models import (
    AuditLog,
    Conversation,
    DocumentChunk,
    ReviewedCase,
    ServiceCase,
    SourceDocument,
    UserAccount,
    utcnow,
)
from .services.agent import SupportAgent
from .services.auth import current_user
from .services.backups import archive_source
from .services.cache import get_retrieval_cache
from .services.documents import PageText, chunk_pages
from .services.rag import RAGService

router = APIRouter(prefix="/service", tags=["服务工作台"])
DB = Annotated[AsyncSession, Depends(get_db)]
Config = Annotated[Settings, Depends(get_settings)]
User = Annotated[UserAccount | None, Depends(current_user)]


def identity(user: UserAccount | None) -> str:
    return user.id if user else "demo-user"


def check_admin(user: UserAccount | None) -> None:
    if user is None or user.role != "admin":
        raise HTTPException(403, "需要管理员权限")


async def owned_case(case_id: str, db: AsyncSession, user: UserAccount | None) -> ServiceCase:
    case = await db.get(ServiceCase, case_id)
    if not case or (case.user_id != identity(user) and not (user and user.role == "admin")):
        raise HTTPException(404, "排查记录不存在")
    return case


def view(case: ServiceCase) -> dict:
    return {
        key: getattr(case, key)
        for key in (
            "id",
            "symptom",
            "device",
            "status",
            "observations",
            "answer",
            "citations",
            "cause",
            "solution",
            "verification",
            "revision",
            "created_at",
            "session_id",
        )
    }


class StartCase(BaseModel):
    symptom: str = Field(min_length=2, max_length=1200)
    device: str = Field(default="", max_length=120)
    session_id: str | None = None


class Observation(BaseModel):
    revision: int = Field(ge=0)
    result: str = Field(min_length=2, max_length=600)


class Resolution(BaseModel):
    revision: int = Field(ge=0)
    cause: str = Field(min_length=2, max_length=800)
    solution: str = Field(min_length=2, max_length=1000)
    verification: str = Field(min_length=2, max_length=800)


class Review(BaseModel):
    approve: bool
    comment: str = Field(default="", max_length=600)


async def update_case(case: ServiceCase, db: AsyncSession, revision: int, **values) -> None:
    changed = await db.execute(
        update(ServiceCase)
        .where(
            ServiceCase.id == case.id,
            ServiceCase.revision == revision,
            ServiceCase.status == "open",
        )
        .values(**values, revision=revision + 1)
    )
    if changed.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, "记录已更新或已结束，请刷新后重试")
    await db.commit()
    await db.refresh(case)


@router.post("/cases", status_code=201)
async def start_case(body: StartCase, db: DB, user: User) -> dict:
    if not body.symptom.strip():
        raise HTTPException(422, "请填写故障现象")
    if body.session_id:
        session = await db.get(Conversation, body.session_id)
        if not session or session.user_id != identity(user):
            raise HTTPException(404, "会话不存在")
    case = ServiceCase(
        user_id=identity(user),
        symptom=body.symptom.strip(),
        device=body.device.strip(),
        session_id=body.session_id,
    )
    db.add(case)
    await db.commit()
    await db.refresh(case)
    return view(case)


@router.get("/cases")
async def list_cases(db: DB, user: User) -> dict:
    query = select(ServiceCase)
    if not (user and user.role == "admin"):
        query = query.where(ServiceCase.user_id == identity(user))
    rows = (await db.scalars(query.order_by(ServiceCase.created_at.desc()).limit(100))).all()
    return {"items": [view(case) for case in rows]}


@router.get("/cases/{case_id}")
async def get_case(case_id: str, db: DB, user: User) -> dict:
    case = await owned_case(case_id, db, user)
    result = view(case)
    review = await db.scalar(select(ReviewedCase).where(ReviewedCase.service_case_id == case.id))
    result["review_status"] = review.status if review else None
    result["review_comment"] = review.comment if review else ""
    return result


@router.post("/cases/{case_id}/observations")
async def observe(case_id: str, body: Observation, db: DB, user: User) -> dict:
    case = await owned_case(case_id, db, user)
    if not body.result.strip():
        raise HTTPException(422, "请填写检查结果")
    if len(case.observations) >= 50:
        raise HTTPException(422, "记录已达50步，请先整理当前处理结果")
    observations = [
        *case.observations,
        {"result": body.result.strip(), "time": utcnow().isoformat()},
    ]
    await update_case(case, db, body.revision, observations=observations, answer="", citations=[])
    return view(case)


@router.post("/cases/{case_id}/next")
async def next_step(case_id: str, db: DB, settings: Config, user: User) -> dict:
    case = await owned_case(case_id, db, user)
    if case.status != "open":
        raise HTTPException(409, "排查已结束")
    revision = case.revision
    # Structured facts, not previous model guesses, form the follow-up question.
    recent = case.observations[-3:]
    results = "\n".join(f"- {item['result']}" for item in recent)
    prompt = (
        f"现场问题：{case.symptom}\n设备：{case.device or '尚未确认'}\n"
        f"已检查结果（最近三步，共{len(case.observations)}步）：\n{results or '尚未检查'}\n"
        "请结合这些检查结果给出下一项检查和不同结果对应的后续方向。"
        "最多三项，先给无需改动设备的检查。不要重复已完成的检查；"
        "不要把推测当根因、自动执行控制或把未验证操作当有效方法。"
        "缺少设备类型或依据时先问清楚，并说明需要补充什么。"
    )
    # Separate generated conversation avoids mixing an unrelated earlier chat into a case.
    result = await SupportAgent(db, settings).respond(prompt, None, identity(user))
    changed = await db.execute(
        update(ServiceCase)
        .where(
            ServiceCase.id == case.id,
            ServiceCase.revision == revision,
            ServiceCase.status == "open",
        )
        .values(
            answer=result.answer,
            citations=[item.model_dump(mode="json") for item in result.citations],
        )
    )
    if changed.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, "生成期间记录发生变化，请刷新并重新生成下一步")
    await db.commit()
    await db.refresh(case)
    return {**view(case), "answer_status": result.status}


@router.post("/cases/{case_id}/resolve")
async def resolve(case_id: str, body: Resolution, db: DB, user: User) -> dict:
    case = await owned_case(case_id, db, user)
    if not all(value.strip() for value in (body.cause, body.solution, body.verification)):
        raise HTTPException(422, "请填写实际原因、有效处理和验证结果")
    await update_case(
        case,
        db,
        body.revision,
        status="resolved",
        cause=body.cause.strip(),
        solution=body.solution.strip(),
        verification=body.verification.strip(),
    )
    return view(case)


@router.post("/cases/{case_id}/submit", status_code=201)
async def submit(case_id: str, db: DB, user: User) -> dict:
    case = await owned_case(case_id, db, user)
    if case.status != "resolved":
        raise HTTPException(409, "先填写解决与验证结果，再提交审核")
    review = ReviewedCase(service_case_id=case.id)
    db.add(review)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "此案例已经提交审核") from exc
    return {"id": review.id, "status": review.status}


@router.get("/reviews")
async def reviews(db: DB, user: User) -> dict:
    check_admin(user)
    rows = (
        await db.execute(
            select(ReviewedCase, ServiceCase)
            .join(ServiceCase, ReviewedCase.service_case_id == ServiceCase.id)
            .order_by(ReviewedCase.created_at.desc())
            .limit(100)
        )
    ).all()
    return {
        "items": [
            {
                "review_id": review.id,
                "review_status": review.status,
                "review_comment": review.comment,
                **view(case),
            }
            for review, case in rows
        ]
    }


def case_text(case: ServiceCase, kind: str = "report") -> str:
    resolved = case.status == "resolved"
    if kind == "reply":
        return f"关于您反馈的“{case.symptom}”：\n" + (
            f"确认原因：{case.cause}\n已完成处理：{case.solution}\n验证结果：{case.verification}"
            if resolved
            else "目前仍在排查，尚未确认根因。\n已核查：\n"
            + "\n".join(item["result"] for item in case.observations)
        )
    title = {"report": "现场服务报告", "knowledge": "已验证服务案例"}[kind]
    checks = (
        "\n".join(
            f"{index}. {item['time']}：{item['result']}"
            for index, item in enumerate(case.observations, 1)
        )
        or "尚无检查记录"
    )
    return (
        f"# {title}\n\n记录编号：{case.id}\n创建时间：{case.created_at.isoformat()}\n"
        f"设备：{case.device or '待补充'}\n"
        f"状态：{'已解决（人员确认）' if resolved else '未解决'}\n\n"
        f"## 故障现象\n{case.symptom}\n\n## 实际检查记录\n{checks}\n\n"
        f"## 确认原因\n{case.cause or '尚未确认'}\n\n## 有效处理\n{case.solution or '尚未确认'}\n\n"
        f"## 验证结果\n{case.verification or '尚未验证'}\n"
    )


@router.post("/reviews/{review_id}")
async def review_case(review_id: str, body: Review, db: DB, settings: Config, user: User) -> dict:
    check_admin(user)
    review = await db.get(ReviewedCase, review_id)
    if not review:
        raise HTTPException(404, "待审案例不存在")
    claimed = await db.execute(
        update(ReviewedCase)
        .where(ReviewedCase.id == review_id, ReviewedCase.status == "pending")
        .values(status="processing")
    )
    if claimed.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, "此案例已经审核")
    case = await db.get(ServiceCase, review.service_case_id)
    if body.approve:
        text = case_text(case, "knowledge")
        document = SourceDocument(
            filename=f"已验证案例-{case.id}.md",
            content_type="text/markdown",
            checksum=hashlib.sha256(text.encode()).hexdigest(),
        )
        db.add(document)
        await db.flush()
        pieces = [
            piece.text
            for piece in chunk_pages(
                [PageText(text=text, page=None)], settings.chunk_size, settings.chunk_overlap
            )
        ]
        rag = RAGService(db, settings.chunk_size, settings.chunk_overlap)
        try:
            vectors = await rag.backend.embed_async(pieces)
        except Exception as exc:
            await db.rollback()
            raise HTTPException(503, "案例索引失败，未发布；可稍后重试") from exc
        for position, (piece, vector) in enumerate(zip(pieces, vectors, strict=True)):
            db.add(
                DocumentChunk(
                    document_id=document.id,
                    position=position,
                    content=piece,
                    embedding=vector,
                    chunk_metadata={
                        "filename": document.filename,
                        "visibility": "private",
                        "enabled": True,
                        "case_status": "verified",
                        "corpus_id": settings.retrieval_corpus_id,
                        "embedding_signature": rag.backend.signature,
                    },
                )
            )
        await run_in_threadpool(archive_source, settings, document.filename, text.encode("utf-8"))
        review.document_id = document.id
    review.status = "approved" if body.approve else "rejected"
    review.reviewer = identity(user)
    review.comment = body.comment.strip()
    db.add(AuditLog(actor=identity(user), action="case_" + review.status, resource=case.id))
    await db.commit()
    if body.approve:
        await get_retrieval_cache(settings).invalidate()
    return {"status": review.status, "document_id": review.document_id}


@router.get("/cases/{case_id}/export")
async def export(
    case_id: str, db: DB, user: User, kind: Literal["report", "reply"] = "report"
) -> Response:
    case = await owned_case(case_id, db, user)
    text = case_text(case, kind)
    if kind != "reply" and case.answer:
        text += "\n## 最新排查建议（不等于验证结论）\n" + case.answer + "\n"
        if case.citations:
            text += "\n## 建议引用的资料\n" + "\n".join(
                f"- {item.get('filename', '')}，页码：{item.get('page') or '未标注'}"
                for item in case.citations
            )
    filename = quote(f"服务记录-{kind}-{case.id[:8]}.md")
    return Response(
        text,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "Cache-Control": "no-store",
        },
    )
