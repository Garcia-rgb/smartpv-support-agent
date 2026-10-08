from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Citation(BaseModel):
    document_id: str
    filename: str
    chunk_id: str
    page: int | None = None
    excerpt: str
    score: float
    # 行业标签随引用一起返回：同一句话出自哪一版资料、哪个机型场景，
    # 是这条依据能不能直接用的前提。抽不到就是 None/空列表，不编默认值。
    document_version: str | None = None
    scenario: str | None = None
    protocols: list[str] = []
    device_models: list[str] = []


class VersionConflict(BaseModel):
    """一次检索里出现的某个资料版本，与并列的其他版本一起返回。

    名字沿用设计里的「版本冲突」，但它的判定条件是「分数接近 + 版本不同」，
    并不等于两份资料互相矛盾。所以客户端要把它渲染成**并列提示**：
    列出各版本的说法和来源，提醒按现场型号与厂家正式资料确认，
    而不是替用户挑一个版本下结论。
    """

    document_version: str
    document_ids: list[str]
    filenames: list[str]
    excerpts: list[str]
    score: float


class DocumentResponse(BaseModel):
    id: str
    filename: str
    chunks: int
    duplicate: bool = False


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class AuthResponse(BaseModel):
    user_id: str
    username: str
    role: Literal["admin", "user"]
    csrf_token: str


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)


class UserStatusRequest(BaseModel):
    active: bool


class ManagedUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    username: str
    role: Literal["user"]
    active: bool
    created_at: datetime


class ManagedUserList(BaseModel):
    items: list[ManagedUser]


class UserCredentialResponse(BaseModel):
    user: ManagedUser
    initial_password: str


class PointTableGenerateRequest(BaseModel):
    direction: Literal["south", "north"]
    fields: dict[str, str] = Field(default_factory=dict)
    points: list[dict[str, str]] = Field(min_length=1, max_length=500)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    user_id: str = Field(default="demo-user", min_length=1, max_length=64)


class QuizResponse(BaseModel):
    status: Literal["answered", "insufficient_evidence", "needs_review"]
    question_type: Literal["判断", "单选", "多选", "未知"]
    question: str
    options: dict[str, str]
    selected_options: list[str]
    explanation: str
    citations: list[Citation]
    recognized_text: str
    session_id: str | None = None
    message_id: str | None = None


class PendingAction(BaseModel):
    action: Literal["create_ticket"]
    confirmation_token: str
    summary: str


# 一轮对话的结局。状态由服务端判定，模型无权声明自己这一轮属于哪一种。
# 调用方拿它来决定下一步：能不能重试、要不要弹确认框、要不要让用户补充信息。
ChatStatus = Literal[
    "completed",  # 正常完成，回答可用
    "degraded",  # 模型不可用，返回的是服务端兜底话术
    "pending_confirmation",  # 写操作被拦下，等待用户确认
    "needs_clarification",  # 问题用了语料里没有的措辞或术语，需要用户补充信息后重问
    "blocked",  # 安全策略拦截，模型一次都没被调用
    "failed",  # 跑完了但给不出有效结果（例如检索没有任何依据）
]

# 回答的依据来自哪里。和 status 是两个维度：status 说「这一轮成功了吗」，
# answer_source 说「这句话的依据是什么」，评测要按后者分层统计命中率。
AnswerSource = Literal[
    "knowledge",  # 来自知识库检索片段，会同时给出 citations
    "tool",  # 来自设备档案、计算器这类结构化工具
    "model",  # 模型自己的回答，没有引用也没有工具结果
    "policy",  # 服务端规则直接给的话术（拦截、追问、待确认）
    "fallback",  # 模型不可用时的降级话术
]


class Clarification(BaseModel):
    """命中语料范围判据时，说明「为什么判为需要补充」以及「建议补什么」。

    和 `PendingAction` 的区别在于等的东西不同：那个在等一个同意/拒绝，
    这个在等一段新信息（型号、现象），所以不能复用 `pending_confirmation` 的状态。
    """

    # missing_terminology：问题里的词组在整份语料里几乎找不到，多半是口语化说法；
    # unknown_foreign_terms：查询里的外文词一个都不认识，多半真的不在资料范围内。
    reason: Literal["missing_terminology", "unknown_foreign_terms"]
    # 客户端可以直接渲染成「建议补充」的条目。
    hints: list[str]


class ChatResponse(BaseModel):
    session_id: str
    message_id: str
    user_message_id: str | None = None
    status: ChatStatus = "completed"
    answer: str
    answer_source: AnswerSource = "model"
    privacy_scope: Literal["public", "private"] | None = None
    citations: list[Citation] = []
    pending_action: PendingAction | None = None
    # 只有 needs_clarification 时才有值：说明判定原因与建议补充的信息。
    # 客户端据此把「回答」渲染成一次追问，而不是一次失败。
    clarification: Clarification | None = None
    # 只有 degraded 且模型层判定为暂时性错误时才为真，客户端据此决定要不要自动重试。
    retryable: bool = False
    # 本次回答的依据来自两个及以上资料版本时，并列列出，由用户按现场版本取舍。
    # 为空表示依据集中在同一版本，不存在需要用户裁决的版本差异。
    conflicts: list[VersionConflict] = []


class UnifiedInputResponse(BaseModel):
    kind: Literal["quiz", "chat"]
    recognized_text: str | None = None
    quiz: QuizResponse | None = None
    chat: ChatResponse | None = None


class MessageImageView(BaseModel):
    id: str
    content_type: str


class MessageView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    role: str
    content: str
    citations: list[dict]
    created_at: datetime
    images: list[MessageImageView] = Field(default_factory=list)


class SessionResponse(BaseModel):
    id: str
    user_id: str
    created_at: datetime
    messages: list[MessageView]


class SessionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    created_at: datetime
    title: str = ""


class SessionListResponse(BaseModel):
    items: list[SessionSummary]
    total: int
    limit: int
    offset: int


class FeedbackRequest(BaseModel):
    message_id: str
    rating: int = Field(ge=-1, le=1)
    comment: str | None = Field(default=None, max_length=1000)


class TicketCreateRequest(BaseModel):
    confirmation_token: str
    user_id: str = "demo-user"


class TicketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    session_id: str
    device_sn: str | None
    reason: str
    status: str


class EvaluationResponse(BaseModel):
    run_id: str
    # 这次评测用的是哪个模型。不同模型跑出来的分数不可比，必须随结果一起返回。
    model: str
    # 样本口径：三层全部通过才算这条样本通过。
    total: int
    passed: int
    score: float
    # 分层口径：{层名: {total, passed, score}}，只统计声明了该层期望的样本。
    layers: dict
    details: list[dict]
