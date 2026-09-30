import hashlib
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from . import __version__
from .config import Settings, get_settings
from .db import create_schema, get_db
from .models import (
    AuditLog,
    ConsumedConfirmationToken,
    Conversation,
    Feedback,
    Message,
    Ticket,
)
from .observability import request_observability
from .schemas import (
    ChatRequest,
    ChatResponse,
    DocumentResponse,
    EvaluationResponse,
    FeedbackRequest,
    QuizResponse,
    SessionListResponse,
    SessionResponse,
    TicketCreateRequest,
    TicketResponse,
)
from .services.agent import SupportAgent
from .services.cache import close_store, get_rate_limiter, get_store
from .services.evaluation import describe_model, run_evaluation, summarize_layers
from .services.llm import OpenAICompatibleClient
from .services.privacy import classify_question
from .services.quiz import answer_question, recognize_image
from .services.rag import RAGService
from .services.security import verify_confirmation_token


def locate_static_dir() -> Path | None:
    """找到内置客户端页面所在目录；找不到就返回 None，纯 API 模式照常工作。

    装成 wheel 后包在 site-packages 里，页面不在包的旁边；容器里工作目录才是 /app。
    所以按「环境变量 → 仓库布局 → 当前工作目录 → 包目录」的顺序找，而不是写死一个相对路径。
    """
    candidates = []
    configured = os.environ.get("SMARTPV_STATIC_DIR")
    if configured:
        candidates.append(Path(configured))
    package_dir = Path(__file__).resolve().parent
    candidates.append(package_dir.parents[1] / "static")  # 仓库布局：<repo>/static
    candidates.append(Path.cwd() / "static")  # 容器与源码目录直接启动
    candidates.append(package_dir / "static")  # 打包时若把页面放进包内
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return None


STATIC_DIR = locate_static_dir()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """应用启动时创建数据库表，关闭时释放缓存连接。"""
    await create_schema()
    yield
    # Redis 没配时这是空操作；配了就必须关，否则测试和热重载会攒下一堆连接。
    await close_store()


app = FastAPI(
    title="光伏电站技术支持 Agent",
    version=__version__,
    description="光伏电站技术支持 Agent：RAG、工具调用、人工确认、评测与审计",
    lifespan=lifespan,
)
app.middleware("http")(request_observability)

DbDep = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
UploadDep = Annotated[UploadFile, File()]
OptionalQuizUpload = Annotated[UploadFile | None, File()]
OptionalQuizText = Annotated[str | None, Form()]
UserHeader = Annotated[str, Header()]

# 上面的类型别名同时描述参数类型和 FastAPI 的依赖来源，减少接口中的重复代码。


@app.get("/health")
async def health(settings: SettingsDep) -> dict:
    # 把向量后端报出来：它决定了库里存的是哈希向量还是语义向量，
    # 排查「检索结果不对」时这是第一个要确认的事。
    # 缓存状态同理：显示降级时，命中率下降和限流变松都是预期内的，
    # 不用去代码里猜现在到底走的哪条路。
    return {
        "status": "ok",
        "version": __version__,
        "environment": settings.app_env,
        "llm_enabled": settings.llm_enabled,
        "local_llm_enabled": settings.local_llm_enabled,
        "privacy_routing_enabled": settings.privacy_routing_enabled,
        "embedding_backend": settings.embedding_backend,
        "embedding_dimension": settings.vector_dimension,
        "cache": get_store(settings).describe(),
        "rate_limit": {
            "requests": settings.rate_limit_requests,
            "window_seconds": settings.rate_limit_window_seconds,
        },
    }


@app.post("/documents", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadDep,
    db: DbDep,
    settings: SettingsDep,
) -> DocumentResponse:
    filename = Path(file.filename or "").name
    if not filename:
        raise HTTPException(400, "文件名不能为空")
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, "文件超过大小限制")
    try:
        document, chunks, duplicate = await RAGService(
            db, settings.chunk_size, settings.chunk_overlap
        ).ingest(filename, file.content_type or "application/octet-stream", data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return DocumentResponse(
        id=document.id, filename=document.filename, chunks=chunks, duplicate=duplicate
    )


@app.post("/quiz/analyze", response_model=QuizResponse)
async def analyze_quiz(
    db: DbDep,
    settings: SettingsDep,
    file: OptionalQuizUpload = None,
    recognized_text: OptionalQuizText = None,
) -> QuizResponse:
    """图片只在本机内存中识别；可提交修正后的文字重新分析。"""
    if recognized_text:
        raw_text = recognized_text.strip()
    elif file:
        if file.content_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise HTTPException(400, "请上传 PNG、JPG 或 WebP 图片")
        data = await file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, "图片超过大小限制")
        try:
            raw_text = await run_in_threadpool(recognize_image, data)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc
    else:
        raise HTTPException(400, "请上传题目截图或填写识别文字")
    try:
        decision = (
            await classify_question(raw_text, RAGService(db), settings)
            if settings.privacy_routing_enabled else None
        )
        public = decision is not None and decision.scope == "public"
        return await answer_question(
            db,
            raw_text,
            chunk_size=settings.chunk_size,
            overlap=settings.chunk_overlap,
            llm_client=OpenAICompatibleClient(
                settings, local=settings.privacy_routing_enabled and not public
            ),
            visibility="public" if public else None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    db: DbDep,
    settings: SettingsDep,
    response: Response,
) -> ChatResponse:
    # 限流放在最前面：被拒的请求不该产生会话、消息和模型调用。
    # 一次 /chat 可能触发多轮模型往返，是整套接口里最贵的那一个。
    decision = await get_rate_limiter(settings).check(request.user_id)
    if not decision.allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"请求过于频繁，请 {decision.retry_after} 秒后重试",
            headers=decision.headers,
        )
    response.headers.update(decision.headers)
    try:
        return await SupportAgent(db, settings).respond(
            request.message, request.session_id, request.user_id
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


LimitQuery = Annotated[int, Query(ge=1, le=100)]
OffsetQuery = Annotated[int, Query(ge=0)]
DatasetPathQuery = Annotated[str, Query(description="评测数据集 JSONL 文件路径")]


@app.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    db: DbDep,
    x_user_id: UserHeader = "demo-user",
    limit: LimitQuery = 10,
    offset: OffsetQuery = 0,
) -> SessionListResponse:
    total = await db.scalar(
        select(func.count()).select_from(Conversation).where(Conversation.user_id == x_user_id)
    )

    conversations = (
        await db.scalars(
            select(Conversation)
            .where(Conversation.user_id == x_user_id)
            .order_by(
                Conversation.created_at.desc(),
                Conversation.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    ).all()

    return SessionListResponse(
        items=conversations,
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@app.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    db: DbDep,
    x_user_id: UserHeader = "demo-user",
) -> SessionResponse:
    conversation = await db.scalar(
        select(Conversation).where(Conversation.id == session_id, Conversation.user_id == x_user_id)
    )
    if not conversation:
        raise HTTPException(404, "会话不存在")
    messages = (
        await db.scalars(
            select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
        )
    ).all()
    return SessionResponse(
        id=conversation.id,
        user_id=conversation.user_id,
        created_at=conversation.created_at,
        messages=messages,
    )


@app.post("/feedback", status_code=status.HTTP_201_CREATED)
async def create_feedback(request: FeedbackRequest, db: DbDep) -> dict:
    if not await db.get(Message, request.message_id):
        raise HTTPException(404, "消息不存在")
    feedback = Feedback(**request.model_dump())
    db.add(feedback)
    await db.commit()
    await db.refresh(feedback)
    return {"id": feedback.id}


@app.post("/tickets", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    request: TicketCreateRequest,
    db: DbDep,
    settings: SettingsDep,
) -> TicketResponse:
    try:
        payload = verify_confirmation_token(
            request.confirmation_token, settings.confirmation_secret
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if payload.get("action") != "create_ticket" or payload.get("user_id") != request.user_id:
        raise HTTPException(403, "确认令牌与当前操作或用户不匹配")
    token_hash = hashlib.sha256(request.confirmation_token.encode()).hexdigest()
    # 先占位、再建单：把「这个令牌用过没有」变成一次主键冲突判定。
    # 只存哈希不存原文——令牌本身就是凭证，落库等于多留一份可用的口令。
    consumed = ConsumedConfirmationToken(token_hash=token_hash, user_id=request.user_id)
    db.add(consumed)
    try:
        await db.flush()
    except IntegrityError as exc:
        # 唯一约束替我们完成了原子判定：并发下第二个请求必然撞在这里。
        await db.rollback()
        raise HTTPException(409, "确认令牌已经使用") from exc

    ticket = Ticket(
        session_id=payload["session_id"],
        device_sn=payload.get("device_sn"),
        reason=payload["reason"],
    )
    db.add(ticket)
    await db.flush()
    consumed.ticket_id = ticket.id
    db.add(
        AuditLog(
            actor=request.user_id,
            action="confirmation_consumed",
            resource=token_hash,
            detail={"ticket_id": ticket.id},
        )
    )
    await db.commit()
    await db.refresh(ticket)
    return ticket


@app.post("/evaluations/run", response_model=EvaluationResponse)
async def evaluation(
    db: DbDep, settings: SettingsDep, dataset_path: DatasetPathQuery
) -> EvaluationResponse:
    # 评测集不进仓库：它是随知识库变化的，由调用方指定路径，避免接口写死一份语料。
    try:
        run = await run_evaluation(db, Path(dataset_path), settings=settings)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc)) from exc
    return EvaluationResponse(
        run_id=run.id,
        model=describe_model(settings, None),
        total=run.total,
        passed=run.passed,
        score=run.score,
        layers=summarize_layers(run.details),
        details=run.details,
    )


# 下面两个路由必须放在所有 API 之后注册：静态挂载一旦落在 / 上会遮蔽后面的接口。
# 页面本身不含任何业务逻辑，只是把上面的接口串成一个可点击的界面，方便本地演示。
if STATIC_DIR is not None:
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def client() -> FileResponse:
        """返回简易客户端页面。"""
        return FileResponse(STATIC_DIR / "index.html")
