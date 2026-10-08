import asyncio
import hashlib
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import quote, urlsplit

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
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
from .admin_routes import router as admin_router
from .config import Settings, get_settings, validate_runtime_settings
from .db import create_schema, get_db
from .models import (
    AuditLog,
    ConsumedConfirmationToken,
    Conversation,
    Feedback,
    LoginSession,
    Message,
    Ticket,
    UserAccount,
)
from .observability import configure_file_logging, request_observability
from .schemas import (
    AuthResponse,
    ChatRequest,
    ChatResponse,
    DocumentResponse,
    EvaluationResponse,
    FeedbackRequest,
    LoginRequest,
    ManagedUser,
    ManagedUserList,
    PasswordChangeRequest,
    PointTableGenerateRequest,
    QuizResponse,
    SessionListResponse,
    SessionResponse,
    TicketCreateRequest,
    TicketResponse,
    UnifiedInputResponse,
    UserCreateRequest,
    UserCredentialResponse,
    UserStatusRequest,
)
from .services.agent import SupportAgent
from .services.auth import (
    COOKIE_NAME,
    SESSION_SECONDS,
    admin_user,
    csrf_token,
    current_user,
    hash_password,
    token_hash,
    verify_password,
)
from .services.backups import archive_source
from .services.cache import close_store, get_rate_limiter, get_store
from .services.evaluation import describe_model, run_evaluation, summarize_layers
from .services.input_routing import is_quiz
from .services.llm import OpenAICompatibleClient
from .services.pointtable import MAX_BYTES as MAX_PROTOCOL_BYTES
from .services.pointtable import PointTableError, generate_csv, parse_protocol
from .services.privacy import classify_question
from .services.quiz import answer_question, recognize_image
from .services.rag import RAGService
from .services.security import verify_confirmation_token
from .services.semantic import EmbeddingUnavailableError


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
    settings = get_settings()
    validate_runtime_settings(settings)
    configure_file_logging(settings.log_dir)
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
app.include_router(admin_router)

DbDep = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
UploadDep = Annotated[UploadFile, File()]
OptionalQuizUpload = Annotated[UploadFile | None, File()]
OptionalQuizText = Annotated[str | None, Form()]
UserHeader = Annotated[str, Header()]
AuthenticatedUser = Annotated[UserAccount | None, Depends(current_user)]
AdminUser = Annotated[UserAccount | None, Depends(admin_user)]

# 上面的类型别名同时描述参数类型和 FastAPI 的依赖来源，减少接口中的重复代码。


@app.post("/auth/login", response_model=AuthResponse)
async def login(
    credentials: LoginRequest,
    request: Request,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
) -> AuthResponse:
    if not settings.auth_enabled:
        raise HTTPException(404, "登录未启用")
    login_origin = request.client.host if request.client else "unknown"
    rate = await get_rate_limiter(settings).check(f"login:{login_origin}")
    if not rate.allowed:
        raise HTTPException(429, "登录尝试过于频繁，请稍后再试", headers=rate.headers)
    account = await db.scalar(
        select(UserAccount).where(UserAccount.username == credentials.username)
    )
    if not account or not account.active or not verify_password(
        credentials.password, account.password_hash
    ):
        raise HTTPException(401, "账号或密码错误")
    token = secrets.token_urlsafe(32)
    db.add(LoginSession(
        token_hash=token_hash(token), user_id=account.id,
        expires_at=int(time.time()) + SESSION_SECONDS,
    ))
    db.add(AuditLog(actor=account.id, action="login", resource="session"))
    await db.commit()
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        COOKIE_NAME, token, max_age=SESSION_SECONDS,
        httponly=True, secure=settings.app_env != "development" or request.url.scheme == "https",
        samesite="lax", path="/",
    )
    return AuthResponse(
        user_id=account.id, username=account.username, role=account.role,
        csrf_token=csrf_token(token, settings.confirmation_secret),
    )


@app.get("/auth/me", response_model=AuthResponse)
async def auth_me(
    request: Request, response: Response, user: AuthenticatedUser, settings: SettingsDep,
) -> AuthResponse:
    if user is None:
        raise HTTPException(401, "请先登录")
    response.headers["Cache-Control"] = "no-store"
    return AuthResponse(
        user_id=user.id, username=user.username, role=user.role,
        csrf_token=csrf_token(request.cookies[COOKIE_NAME], settings.confirmation_secret),
    )


@app.post("/auth/logout", status_code=204)
async def logout(
    request: Request, response: Response, user: AuthenticatedUser, db: DbDep,
) -> None:
    if user is None:
        raise HTTPException(401, "请先登录")
    login_session = await db.get(LoginSession, token_hash(request.cookies[COOKIE_NAME]))
    if login_session:
        await db.delete(login_session)
        await db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")


@app.post("/auth/password", status_code=204)
async def change_password(
    body: PasswordChangeRequest,
    response: Response,
    user: AuthenticatedUser,
    db: DbDep,
) -> None:
    if user is None:
        raise HTTPException(401, "请先登录")
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "当前密码错误")
    user.password_hash = hash_password(body.new_password)
    sessions = (await db.scalars(
        select(LoginSession).where(LoginSession.user_id == user.id)
    )).all()
    for session in sessions:
        await db.delete(session)
    db.add(AuditLog(actor=user.id, action="password_changed", resource=user.id))
    await db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")


def _user_name(raw: str) -> str:
    name = raw.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,31}", name):
        raise HTTPException(400, "登录名须为 3–32 位字母、数字、点、下划线或短横线")
    return name


async def _revoke_logins(db: AsyncSession, user_id: str) -> None:
    sessions = (await db.scalars(
        select(LoginSession).where(LoginSession.user_id == user_id)
    )).all()
    for session in sessions:
        await db.delete(session)


async def _managed_user(db: AsyncSession, user_id: str) -> UserAccount:
    user = await db.get(UserAccount, user_id)
    if not user or user.role != "user":
        raise HTTPException(404, "用户不存在")
    return user


@app.get("/admin/users", response_model=ManagedUserList)
async def list_users(db: DbDep, admin: AdminUser) -> ManagedUserList:
    if admin is None:
        raise HTTPException(403, "需要管理员权限")
    users = (await db.scalars(
        select(UserAccount).where(UserAccount.role == "user")
        .order_by(UserAccount.created_at.desc(), UserAccount.username)
    )).all()
    return ManagedUserList(items=users)


@app.post("/admin/users", response_model=UserCredentialResponse, status_code=201)
async def create_user(
    body: UserCreateRequest, response: Response, db: DbDep, admin: AdminUser,
) -> UserCredentialResponse:
    if admin is None:
        raise HTTPException(403, "需要管理员权限")
    username = _user_name(body.username)
    if await db.scalar(select(UserAccount.id).where(UserAccount.username == username)):
        raise HTTPException(409, "登录名已存在")
    password = secrets.token_urlsafe(12)
    user = UserAccount(username=username, password_hash=hash_password(password), role="user")
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "登录名已存在") from exc
    db.add(AuditLog(actor=admin.id, action="user_created", resource=user.id))
    await db.commit()
    await db.refresh(user)
    response.headers["Cache-Control"] = "no-store"
    return UserCredentialResponse(user=user, initial_password=password)


@app.patch("/admin/users/{user_id}/status", response_model=ManagedUser)
async def set_user_status(
    user_id: str, body: UserStatusRequest, db: DbDep, admin: AdminUser,
) -> ManagedUser:
    if admin is None:
        raise HTTPException(403, "需要管理员权限")
    user = await _managed_user(db, user_id)
    user.active = body.active
    if not body.active:
        await _revoke_logins(db, user.id)
    db.add(AuditLog(
        actor=admin.id, action="user_enabled" if body.active else "user_disabled",
        resource=user.id,
    ))
    await db.commit()
    await db.refresh(user)
    return ManagedUser.model_validate(user)


@app.post("/admin/users/{user_id}/reset-password", response_model=UserCredentialResponse)
async def reset_user_password(
    user_id: str, response: Response, db: DbDep, admin: AdminUser,
) -> UserCredentialResponse:
    if admin is None:
        raise HTTPException(403, "需要管理员权限")
    user = await _managed_user(db, user_id)
    password = secrets.token_urlsafe(12)
    user.password_hash = hash_password(password)
    await _revoke_logins(db, user.id)
    db.add(AuditLog(actor=admin.id, action="user_password_reset", resource=user.id))
    await db.commit()
    await db.refresh(user)
    response.headers["Cache-Control"] = "no-store"
    return UserCredentialResponse(user=user, initial_password=password)


@app.post("/point-tables/parse")
async def parse_point_table_protocol(
    user: AuthenticatedUser, file: UploadDep, direction: Annotated[str, Form()],
) -> dict:
    if user is None:
        raise HTTPException(403, "需要登录账号")
    data = await file.read(MAX_PROTOCOL_BYTES + 1)
    try:
        result = await run_in_threadpool(parse_protocol, file.filename or "", data, direction)
    except PointTableError as exc:
        raise HTTPException(422, str(exc)) from exc
    return result


@app.post("/point-tables/generate")
async def make_point_table(
    body: PointTableGenerateRequest, user: AuthenticatedUser, db: DbDep,
) -> Response:
    if user is None:
        raise HTTPException(403, "需要登录账号")
    try:
        content, filename = generate_csv(body.direction, body.fields, body.points)
    except PointTableError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.add(AuditLog(actor=user.id, action="point_table_generated", resource=body.direction,
                    detail={"points": len(body.points)}))
    await db.commit()
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                "attachment; filename=point-table.csv; filename*=UTF-8''" + quote(filename)
            ),
            "Cache-Control": "no-store",
        },
    )


@app.get("/health")
async def health(settings: SettingsDep) -> dict:
    # 把向量后端报出来：它决定了库里存的是哈希向量还是语义向量，
    # 排查「检索结果不对」时这是第一个要确认的事。
    # 缓存状态同理：显示降级时，命中率下降和限流变松都是预期内的，
    # 不用去代码里猜现在到底走的哪条路。
    local_llm_reachable = False
    if settings.local_llm_enabled:
        parsed = urlsplit(settings.local_llm_base_url or "")
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(parsed.hostname, parsed.port or 80), timeout=0.5
            )
            writer.close()
            await writer.wait_closed()
            local_llm_reachable = True
        except (OSError, TimeoutError):
            pass
    return {
        "status": "ok",
        "version": __version__,
        "environment": settings.app_env,
        "llm_enabled": settings.llm_enabled,
        "local_llm_enabled": settings.local_llm_enabled,
        "local_llm_reachable": local_llm_reachable,
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
    _admin: AdminUser,
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
    except EmbeddingUnavailableError as exc:
        raise HTTPException(503, f"本地检索未就绪：{exc}") from exc
    await run_in_threadpool(archive_source, settings, filename, data)
    db.add(AuditLog(actor=_admin.id if _admin else "system", action="document_imported",
                    resource=document.id, detail={"chunks": chunks, "duplicate": duplicate}))
    await db.commit()
    return DocumentResponse(
        id=document.id, filename=document.filename, chunks=chunks, duplicate=duplicate
    )


@app.post("/quiz/analyze", response_model=QuizResponse)
async def analyze_quiz(
    db: DbDep,
    settings: SettingsDep,
    _user: AuthenticatedUser,
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
            llm_client=OpenAICompatibleClient(settings),
            visibility="public" if public else None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except EmbeddingUnavailableError as exc:
        raise HTTPException(503, f"本地检索未就绪：{exc}") from exc


@app.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    db: DbDep,
    settings: SettingsDep,
    response: Response,
    user: AuthenticatedUser,
) -> ChatResponse:
    # 限流放在最前面：被拒的请求不该产生会话、消息和模型调用。
    # 一次 /chat 可能触发多轮模型往返，是整套接口里最贵的那一个。
    user_id = user.id if user else request.user_id
    decision = await get_rate_limiter(settings).check(user_id)
    if not decision.allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"请求过于频繁，请 {decision.retry_after} 秒后重试",
            headers=decision.headers,
        )
    response.headers.update(decision.headers)
    try:
        return await SupportAgent(db, settings).respond(
            request.message, request.session_id, user_id
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except EmbeddingUnavailableError as exc:
        raise HTTPException(503, f"本地检索未就绪：{exc}") from exc


@app.get("/ready")
async def ready(db: DbDep) -> dict:
    try:
        await db.execute(select(1))
    except Exception as exc:
        raise HTTPException(503, "数据库未就绪") from exc
    return {"status": "ready", "version": __version__}


@app.post("/input", response_model=UnifiedInputResponse)
async def unified_input(
    db: DbDep,
    settings: SettingsDep,
    response: Response,
    user: AuthenticatedUser,
    file: OptionalQuizUpload = None,
    message: Annotated[str | None, Form()] = None,
    session_id: Annotated[str | None, Form()] = None,
) -> UnifiedInputResponse:
    """Normalize image/text input, then route clear exam questions to quiz logic."""
    identity = user.id if user else "demo-user"
    if session_id and not await db.scalar(select(Conversation.id).where(
            Conversation.id == session_id, Conversation.user_id == identity)):
        raise HTTPException(404, "会话不存在")
    prompt = (message or "").strip()
    recognized = None
    if file:
        if file.content_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise HTTPException(400, "请上传 PNG、JPG 或 WebP 图片")
        data = await file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, "图片超过大小限制")
        try:
            recognized = (await run_in_threadpool(recognize_image, data)).strip()
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        if not recognized:
            raise HTTPException(400, "图片中未识别到文字，请换清晰截图或直接输入问题")
    elif not prompt:
        raise HTTPException(400, "请输入问题或上传图片")
    raw = recognized or prompt
    if len(raw) > 4000:
        raise HTTPException(400, "识别文字过长，请裁剪图片或精简问题")
    issue_hint = any(word in prompt for word in ("客户", "现场", "告警", "报错", "排查", "故障"))
    if is_quiz(raw) and not issue_hint:
        identity = user.id if user else "demo-user"
        decision = await get_rate_limiter(settings).check(identity)
        if not decision.allowed:
            raise HTTPException(429, f"请求过于频繁，请 {decision.retry_after} 秒后重试",
                                headers=decision.headers)
        response.headers.update(decision.headers)
        result = await analyze_quiz(db, settings, user, recognized_text=raw)
        saved = await SupportAgent(db, settings).save_quiz(raw, result, session_id, identity)
        result.session_id = saved.session_id
        result.message_id = saved.message_id
        return UnifiedInputResponse(kind="quiz", recognized_text=recognized, quiz=result)
    combined = f"{prompt}\n{recognized}" if prompt and recognized else raw
    if len(combined) > 4000:
        raise HTTPException(400, "识别文字过长，请裁剪图片或精简问题")
    result = await chat(ChatRequest(message=combined, session_id=session_id),
                        db, settings, response, user)
    return UnifiedInputResponse(kind="chat", recognized_text=recognized, chat=result)


LimitQuery = Annotated[int, Query(ge=1, le=100)]
OffsetQuery = Annotated[int, Query(ge=0)]
DatasetPathQuery = Annotated[str, Query(description="评测数据集 JSONL 文件路径")]


@app.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    db: DbDep,
    user: AuthenticatedUser,
    x_user_id: UserHeader = "demo-user",
    limit: LimitQuery = 10,
    offset: OffsetQuery = 0,
) -> SessionListResponse:
    owner_filter = [] if user and user.role == "admin" else [
        Conversation.user_id == (user.id if user else x_user_id)
    ]
    total = await db.scalar(
        select(func.count()).select_from(Conversation).where(*owner_filter)
    )

    conversations = (
        await db.scalars(
            select(Conversation)
            .where(*owner_filter)
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
    user: AuthenticatedUser,
    x_user_id: UserHeader = "demo-user",
) -> SessionResponse:
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == session_id,
            *([] if user and user.role == "admin" else [
                Conversation.user_id == (user.id if user else x_user_id)
            ]),
        )
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
async def create_feedback(
    request: FeedbackRequest, db: DbDep, user: AuthenticatedUser,
) -> dict:
    message = await db.get(Message, request.message_id)
    if not message:
        raise HTTPException(404, "消息不存在")
    if user and user.role != "admin":
        conversation = await db.get(Conversation, message.session_id)
        if not conversation or conversation.user_id != user.id:
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
    user: AuthenticatedUser,
) -> TicketResponse:
    try:
        payload = verify_confirmation_token(
            request.confirmation_token, settings.confirmation_secret
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    user_id = user.id if user else request.user_id
    if payload.get("action") != "create_ticket" or payload.get("user_id") != user_id:
        raise HTTPException(403, "确认令牌与当前操作或用户不匹配")
    if user and user.role != "admin":
        conversation = await db.get(Conversation, payload.get("session_id"))
        if not conversation or conversation.user_id != user.id:
            raise HTTPException(403, "无权操作该会话")
    token_hash = hashlib.sha256(request.confirmation_token.encode()).hexdigest()
    # 先占位、再建单：把「这个令牌用过没有」变成一次主键冲突判定。
    # 只存哈希不存原文——令牌本身就是凭证，落库等于多留一份可用的口令。
    consumed = ConsumedConfirmationToken(token_hash=token_hash, user_id=user_id)
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
            actor=user_id,
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
    db: DbDep, settings: SettingsDep, dataset_path: DatasetPathQuery, _admin: AdminUser,
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
