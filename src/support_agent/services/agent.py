"""把 Agent Loop 接进 /chat 的协调层。

早期版本的 /chat 走的是 `graph.py` 的规则路由：Python 用关键词判断用户
想干什么，再决定调哪个工具。现在这条路径换成了 Agent Loop——由「模型」提出
工具调用申请，服务端负责校验和执行。

这里有两个容易被混淆的概念，代码结构上是分开的：

- **模型**只提出申请。配置了远程模型就用真模型，没配置就用 `RuleBasedLocalModel`，
  两者实现同一个 `chat_with_tools` 接口，因此 Agent Loop 和测试都不必区分它们。
- **规则**仍然由服务端掌握。提示词注入拦截放在循环之外，因为安全判断不能交给模型。
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..models import AuditLog, Conversation, Message
from ..schemas import (
    AnswerSource,
    ChatResponse,
    ChatStatus,
    Citation,
    Clarification,
    PendingAction,
    VersionConflict,
)
from .agent_loop import LoopResult, ToolCallRecord, build_support_registry, run_agent_loop
from .evidence import prepare_evidence_context
from .industry import describe_facets
from .input_routing import is_quiz
from .issue_input import normalize_issue_input
from .llm import LLMError, OpenAICompatibleClient
from .local_model import RuleBasedLocalModel
from .local_summary import summarize_local_retrieval
from .privacy import classify_question, safe_web_query, search_public_web
from .quiz import QuizResponse, answer_question
from .rag import RAGService, SearchHit
from .security import create_confirmation_token, looks_like_prompt_injection
from .tools import (
    DEVICE_SN_PATTERN,
    TICKET_KEYWORDS,
    has_structured_anchor,
    is_calculation_request,
)

INJECTION_ANSWER = "该请求可能试图绕过系统规则，我不能执行。你可以继续咨询公开的业务信息。"
NO_EVIDENCE_ANSWER = "当前知识库没有找到足够可靠的依据，请补充问题信息或转人工确认。"
CONFIRMATION_ANSWER = "创建工单会产生写操作，请确认后再提交。"

# 命中语料范围判据时的话术。这里刻意不拒答：判据是词汇层面的近似，
# 口语化的真问题也会被它拦下（实测 10 条改写问法拦了 7 条），
# 一律回「找不到依据」等于把能答的问题也推走。改成请用户补一句关键信息，
# 追问仍然由服务端给出、模型一次都不调用。
#
# 两种原因分开给话术：措辞对不上（口语化）时补个型号/现象就能对上；
# 外文词一个都不认识时，先讲清「这份资料不覆盖」，再把人引回现场问题。
CLARIFICATION_ANSWERS: dict[str, str] = {
    "missing_terminology": (
        "这个问题我没能定位到对应的资料。换成本领域的说法，或者补一句设备型号、现象就能对上——"
        "型号例如 SUN2000-100KTL-M1、LUNA2000；现象例如报什么故障码、在什么工况下出现、"
        "涉及哪个屏柜或回路。"
    ),
    "unknown_foreign_terms": (
        "你提到的术语不在当前这份光伏资料范围内。如果问的是现场设备，"
        "请补充设备型号（例如 SUN2000-100KTL-M1）或具体现象（故障码、出现时的工况），"
        "我再按对应机型与版本查一次。"
    ),
}
# 追问时一并告诉用户「补什么」，客户端可以直接渲染成提示项。
CLARIFICATION_HINTS = [
    "设备型号（如 SUN2000-100KTL-M1、LUNA2000）",
    "具体现象（故障码 / 出现时的工况 / 涉及的屏柜或回路）",
]
# 单条历史消息最多回填多少字符，避免一次长会话把提示词撑爆。
HISTORY_MESSAGE_CHARS = 2000
# 写进审计日志的参数原文长度上限。
AUDIT_ARGUMENT_CHARS = 500
# 这几个工具返回的是结构化事实；用它们答出来的结果，和「模型凭上下文自己说的」
# 必须分开标注，否则评测无法区分「工具选对了」和「模型自由发挥」。
BUSINESS_TOOLS = ("query_device", "calculator")


def _used_business_tool(result: LoopResult) -> bool:
    """判断这一轮是否真的拿到了结构化工具结果（失败或待确认的调用不算）。"""
    return any(record.ok and record.name in BUSINESS_TOOLS for record in result.tool_calls)


def _local_tool_request(text: str) -> bool:
    """仅让明确的计算、建单或设备状态查询走本地工具流程。"""
    if is_calculation_request(text) or any(word in text for word in TICKET_KEYWORDS):
        return True
    return bool(
        DEVICE_SN_PATTERN.search(text.upper())
        and any(word in text for word in ("运行状态", "设备状态", "现在什么状态"))
    )


def _dedupe_hits(hits: list[SearchHit], top_k: int) -> list[SearchHit]:
    """同一个片段可能被多轮检索命中，按片段去重并按相关度取前若干条。"""
    seen: set[str] = set()
    unique: list[SearchHit] = []
    for hit in hits:
        if hit.chunk.id in seen:
            continue
        seen.add(hit.chunk.id)
        unique.append(hit)
    return sorted(unique, key=lambda item: item.score, reverse=True)[:top_k]


@dataclass
class TurnOutcome:
    """一轮对话算出来的终态，不带任何持久化副作用。

    线上 `/chat` 和离线评测共用这一份判定：评测直接调 `SupportAgent.run_turn`
    拿终态，而不是在评测脚本里再抄一遍分支。抄一遍的代价是两边会各自漂移，
    最后「评测通过」和「线上行为正确」说的不是一回事。
    """

    status: ChatStatus
    answer: str
    answer_source: AnswerSource
    citations: list[Citation] = field(default_factory=list)
    retryable: bool = False
    # 本次实际检索到的片段（已按语料、阈值、去重、top_k 处理），评测的检索层直接用它。
    hits: list[SearchHit] = field(default_factory=list)
    # 依据跨了多个资料版本时并列返回，客户端据此提示用户按现场版本确认。
    conflicts: list[VersionConflict] = field(default_factory=list)
    # 循环轨迹；被安全拦截时不存在，所以是 None 而不是空结果。
    loop: LoopResult | None = None
    # 待确认时带出被拦下的那次写操作，令牌由调用方（需要会话信息）去签。
    confirmation_record: ToolCallRecord | None = None
    # 需要补充信息时带出原因与建议补充的内容，由调用方透传给客户端。
    clarification: Clarification | None = None
    blocked: bool = False
    privacy_scope: str | None = None


class SupportAgent:
    """协调会话、Agent Loop、工具、RAG、模型调用和消息持久化。"""

    def __init__(self, db: AsyncSession, settings: Settings, model: object | None = None) -> None:
        self.db = db
        self.settings = settings
        self.rag = RAGService(db, settings.chunk_size, settings.chunk_overlap)
        self.llm = OpenAICompatibleClient(settings)
        self.local_llm = OpenAICompatibleClient(settings, local=True)
        # 隐私分流启用后，旧 Agent Loop 只能用本地模型处理结构化工具请求。
        self.model = model or (
            RuleBasedLocalModel()
            if settings.privacy_routing_enabled
            else (self.llm if settings.llm_enabled else RuleBasedLocalModel())
        )

    async def _privacy_knowledge_turn(
        self, text: str, question: str | None = None,
    ) -> TurnOutcome:
        """模型调用前确定资料边界；公开调用绝不带历史或内部片段。"""
        question = question or text
        decision = await classify_question(text, self.rag, self.settings)
        if decision.scope == "public":
            hits = decision.public_hits
            contexts = [hit.chunk.content for hit in hits[: self.settings.retrieval_top_k]]
            answer = summarize_local_retrieval(text, hits)
            model = self.llm if self.settings.llm_enabled else self.local_llm
            if model.enabled:
                try:
                    answer = await model.answer(question, contexts)
                except LLMError:
                    pass
            return TurnOutcome(
                status="completed", answer=answer, answer_source="knowledge",
                citations=self.rag.citations(hits), hits=hits,
                conflicts=self.rag.detect_conflicts(hits), privacy_scope="public",
            )

        # 内部问题可检索两类资料；按当前用户授权仅发送本次问题和命中片段。
        hits = await self.retrieve(text)
        if hits and hits[0].score >= 0.35:
            answer = summarize_local_retrieval(text, hits)
            if self.llm.enabled:
                try:
                    answer = await self.llm.answer(
                        question,
                        [prepare_evidence_context(hit.chunk.content, 1200) for hit in hits[:2]]
                    )
                except LLMError:
                    pass
            return TurnOutcome(
                status="completed", answer=answer, answer_source="knowledge",
                citations=self.rag.citations(hits), hits=hits,
                conflicts=self.rag.detect_conflicts(hits), privacy_scope="private",
            )

        safe_query = safe_web_query(text)
        results = []
        if safe_query:
            try:
                results = await search_public_web(safe_query, self.settings)
            except (ValueError, OSError):
                results = []
            except Exception:
                # 搜索服务故障只能降级，不能改用远程模型传原问题。
                results = []
        if results and self.llm.enabled:
            public_contexts = [
                f"公开网页：{item['title']}\n{item['description']}\n{item['url']}"
                for item in results
            ]
            try:
                answer = await self.llm.answer(text, public_contexts)
                answer += "\n\n公开检索来源：\n" + "\n".join(
                    f"- {item['title']}：{item['url']}" for item in results[:3]
                )
                return TurnOutcome(
                    status="completed", answer=answer, answer_source="knowledge",
                    privacy_scope="private",
                )
            except LLMError:
                pass
        return TurnOutcome(
            status="failed",
            answer=(
                "资料不足以确定答案；公开检索未配置、无安全搜索词或未找到可核对的结果。"
                + ("请补充最后采集时间、设备本机读数、采集器在线状态和通信方式；"
                   "需要该厂家型号的协议或经核实的处理记录。"
                   if any(term in text for term in ("不刷新", "不更新", "没数据", "无数据"))
                   else "")
            ),
            answer_source="policy", privacy_scope="private",
        )

    async def _conversation(self, session_id: str | None, user_id: str) -> Conversation:
        """读取已有会话或创建新会话，同时检查会话归属。"""
        conversation = await self.db.get(Conversation, session_id) if session_id else None
        if conversation and conversation.user_id != user_id:
            raise PermissionError("无权访问该会话")
        if session_id and not conversation:
            raise LookupError("会话不存在")
        if not conversation:
            conversation = Conversation(user_id=user_id)
            self.db.add(conversation)
            await self.db.flush()
        return conversation

    async def _save_message(
        self, session_id: str, role: str, content: str, citations: list[dict] | None = None
    ) -> Message:
        message = Message(
            session_id=session_id, role=role, content=content, citations=citations or []
        )
        self.db.add(message)
        await self.db.flush()
        return message

    async def _history(self, session_id: str) -> list[dict[str, str]]:
        """读取本会话此前的消息，按时间正序返回给模型当前轮。"""
        rows = (
            await self.db.scalars(
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(self.settings.chat_history_limit)
            )
        ).all()
        return [
            {"role": item.role, "content": item.content[:HISTORY_MESSAGE_CHARS]}
            for item in reversed(rows)
        ]

    async def retrieve(self, query: str) -> list[SearchHit]:
        """按线上同一套参数检索：限定语料、按阈值过滤、去重后取 top_k。

        评测的检索层也走这里，因此它评的是线上真实用的那一次检索，
        而不是「另写一个不带 corpus_id 的 search」。
        """
        hits = await self.rag.search(
            query,
            self.settings.retrieval_top_k,
            corpus_id=self.settings.retrieval_corpus_id,
            min_score=self.settings.retrieval_min_score,
        )
        return _dedupe_hits(hits, self.settings.retrieval_top_k)

    def _knowledge_searcher(self, sink: list[SearchHit]):
        """构造知识库检索工具的执行函数，同时把命中收集起来用于生成引用。

        返回给模型的每条依据都带一行行业标签（版本 / 场景 / 协议 / 机型）。
        模型必须知道「这条出自哪一版、哪个机型」，否则它面对两份说法不同的资料时
        只能把它们揉成一个答案——而现场最怕的正是这个。标签抽不到就不写，
        不拿「可能是」去凑。
        """

        async def search(query: str) -> str:
            hits = await self.retrieve(query)
            sink.extend(hits)
            if not hits:
                return "知识库中没有找到相关依据。请如实告知用户无法回答，不要编造内容。"
            blocks: list[str] = []
            for index, hit in enumerate(hits):
                facets = describe_facets(hit.chunk.chunk_metadata or {})
                label = f"（依据：{facets}）" if facets else ""
                blocks.append(f"[资料{index + 1}]{label} {hit.chunk.content}")
            return "\n\n".join(blocks)

        return search

    def _confirmation(
        self, outcome: TurnOutcome, session_id: str, user_id: str, text: str
    ) -> PendingAction | None:
        """把循环里被拦下的写操作转成一次人工确认请求。"""
        record = outcome.confirmation_record
        if record is None:
            return None
        arguments = record.parsed or {}
        reason = arguments.get("reason") or text
        token = create_confirmation_token(
            {
                "action": "create_ticket",
                "session_id": session_id,
                "user_id": user_id,
                "device_sn": arguments.get("device_sn"),
                "reason": reason,
            },
            self.settings.confirmation_secret,
        )
        return PendingAction(
            action="create_ticket",
            confirmation_token=token,
            summary=f"创建工单：{reason[:80]}",
        )

    def _audit_tool_calls(self, user_id: str, session_id: str, result: LoopResult) -> None:
        """把工具轨迹写进审计日志；消息表只保留对用户可见的对话内容。"""
        if not result.tool_calls:
            return
        self.db.add(
            AuditLog(
                actor=user_id,
                action="agent_loop_tool_calls",
                resource=session_id,
                detail={
                    "rounds": result.rounds,
                    "stopped_reason": result.stopped_reason,
                    "tools": [
                        {
                            "name": record.name,
                            "ok": record.ok,
                            "arguments": record.arguments[:AUDIT_ARGUMENT_CHARS],
                        }
                        for record in result.tool_calls
                    ],
                },
            )
        )

    async def run_turn(self, text: str, history: list[dict[str, str]]) -> TurnOutcome:
        """跑一轮完整链路并判定终态，**不写数据库**。

        出口有六种状态（见 `schemas.ChatStatus`）。状态一律由服务端判定：
        模型只负责提出工具申请和写一段话，它无权声明这一轮「正常完成」还是「被拦截」。
        下面这段分支的顺序就是状态优先级——同一轮里既被拦过、又检索为空时，
        以更靠前的那一个为准，不会出现「令牌给了但回答是拒答」这种自相矛盾的结果。
        """
        if looks_like_prompt_injection(text):
            # 安全判断不能交给模型：即使模型被说服，这里也必须先拦下来。
            # 这一支连模型都不调用，所以 loop 保持为 None。
            return TurnOutcome(
                status="blocked",
                answer=INJECTION_ANSWER,
                answer_source="policy",
                blocked=True,
            )

        # History keeps the full input; retrieval and generation use the fault summary.
        question = normalize_issue_input(text)
        text = normalize_issue_input(text, include_fields=False)

        if self.settings.privacy_routing_enabled and not _local_tool_request(text):
            return await self._privacy_knowledge_turn(text, question)

        # 语料范围判定必须前置。实测模型遇到明显跑题的问题（「Python 怎么装环境」）
        # 根本不会去调检索工具，它直接凭「我是光伏助手」拒答——于是下面那句
        # 「检索过但没有依据」的兜底永远等不到，判不判定全看模型心情。
        # 这里自己先判一次，把这条抗幻觉路径变成服务端确定性行为，
        # 顺带省掉一次模型调用。判定逻辑与 `RAGService.search` 共用同一份语料统计。
        #
        # 命中之后不拒答，而是请用户补充设备型号或现象：判据是词汇层面的近似，
        # 口语化的真问题同样会撞上它，一律回「找不到依据」会把答得出的问题也推走。
        #
        # 但带结构化锚点的问题先豁免。判据问的是「这个问题属于这份资料吗」，
        # 而带设备序列号、算式或工单诉求的问法由工具承接、压根不查这份资料，
        # 拿同一把尺子量会量错：实测「SN-2024-000123 这台设备现在什么状态」缺失比例 0.62、
        # 「帮我建个工单」0.67，两条都被判成跑题去追问，设备查询与建工单于是永远走不到。
        if not has_structured_anchor(text):
            scope_reason = await self.rag.corpus_scope_reason(
                text, corpus_id=self.settings.retrieval_corpus_id
            )
            if scope_reason is not None:
                return TurnOutcome(
                    status="needs_clarification",
                    answer=CLARIFICATION_ANSWERS[scope_reason],
                    answer_source="policy",
                    clarification=Clarification(
                        reason=scope_reason, hints=list(CLARIFICATION_HINTS)
                    ),
                )

        retrieved: list[SearchHit] = []
        registry = build_support_registry(self._knowledge_searcher(retrieved))
        result = await run_agent_loop(
            self.model,
            text,
            registry=registry,
            max_rounds=self.settings.agent_max_rounds,
            history=history,
        )
        hits = _dedupe_hits(retrieved, self.settings.retrieval_top_k)
        citations = self.rag.citations(hits)
        searched = any(record.name == "search_knowledge_base" for record in result.tool_calls)

        outcome = TurnOutcome(
            status="completed",
            answer=result.answer,
            answer_source="model",
            citations=citations,
            hits=hits,
            # 依据跨版本时并列列出，由用户在客户端按现场版本取舍。
            conflicts=self.rag.detect_conflicts(hits),
            loop=result,
        )
        if result.stopped_reason.startswith("llm_error"):
            # 模型不可用：这段话术是服务端写的，能不能重试由模型层的错误分类决定。
            outcome.status = "degraded"
            outcome.answer_source = "fallback"
            outcome.retryable = result.retryable
        elif result.stopped_reason == "needs_confirmation":
            # 写操作没有被执行，这里只是标出它，令牌由需要会话信息的调用方去签。
            record = next((item for item in result.tool_calls if item.requires_confirmation), None)
            if record is not None:
                outcome.status = "pending_confirmation"
                outcome.answer_source = "policy"
                outcome.answer = CONFIRMATION_ANSWER
                outcome.confirmation_record = record
        elif searched and not hits:
            # 检索过但没有任何依据时，服务端覆盖模型的话术，避免它凭记忆作答。
            outcome.answer = NO_EVIDENCE_ANSWER
            outcome.status = "failed"
            outcome.answer_source = "policy"
        elif citations:
            outcome.answer_source = "knowledge"
            if isinstance(self.model, RuleBasedLocalModel):
                outcome.answer = summarize_local_retrieval(text, hits)
                outcome.citations = self.rag.citations(hits[:3])
        elif _used_business_tool(result):
            outcome.answer_source = "tool"
        return outcome

    async def _try_quiz(self, text: str) -> tuple[str, QuizResponse, str | None] | None:
        """消息能解析成「题干 + 选项」就当作刷题请求处理，否则返回 None 走普通对话。

        返回 (聊天回答文本, 刷题结果)；聊天文本是在 explanation 前面加上明确的选项结论。
        """
        if not is_quiz(text):
            return None
        decision = (
            await classify_question(text, self.rag, self.settings)
            if self.settings.privacy_routing_enabled else None
        )
        public = decision is not None and decision.scope == "public"
        result = await answer_question(
            self.db, text,
            llm_client=self.llm,
            visibility="public" if public else None,
        )
        if result.selected_options:
            head = f"**结论** 选 {'、'.join(result.selected_options)}。"
        else:
            head = "**结论** 资料里没有足够依据确定选项，以下供你核对："
        return f"{head}\n\n{result.explanation}", result, (
            decision.scope if decision else None
        )

    async def save_quiz(
        self, text: str, quiz: QuizResponse, session_id: str | None,
        user_id: str, privacy_scope: str | None = None,
    ) -> ChatResponse:
        conversation = await self._conversation(session_id, user_id)
        chosen = "、".join(quiz.selected_options)
        answer = (f"选 {chosen}。" if chosen else "暂无法确定选项。") + "\n\n" + quiz.explanation
        await self._save_message(conversation.id, "user", text)
        assistant = await self._save_message(conversation.id, "assistant", answer,
                                            [c.model_dump() for c in quiz.citations])
        self.db.add(AuditLog(actor=user_id, action="quiz_answered", resource=conversation.id,
                            detail={"status": quiz.status}))
        await self.db.commit()
        return ChatResponse(session_id=conversation.id, message_id=assistant.id, answer=answer,
                            status=("completed" if quiz.status == "answered"
                                    else "needs_clarification"),
                            answer_source="knowledge" if quiz.citations else "policy",
                            citations=quiz.citations, privacy_scope=privacy_scope)

    async def respond(self, text: str, session_id: str | None, user_id: str) -> ChatResponse:
        """处理一轮用户消息：跑 Agent Loop，并把用户消息和最终回答一起保存。

        判定全部交给 `run_turn`，这里只负责三件带副作用的事：
        建/读会话、写消息、给待确认的写操作签一张令牌。
        """
        # 消息长得像一道题（有题干 + 至少两个选项）时直接走刷题判定，
        # 让手打/粘贴在聊天框的题目也能拿到确定的选项，而不是一段不给结论的话术。
        if session_id:
            await self._conversation(session_id, user_id)
        quiz = await self._try_quiz(text)
        if quiz is not None:
            _, quiz_result, quiz_scope = quiz
            return await self.save_quiz(text, quiz_result, session_id, user_id, quiz_scope)

        conversation = await self._conversation(session_id, user_id)
        # 历史必须在写入本轮用户消息之前读取，否则本轮问题会在上下文里出现两次。
        history = await self._history(conversation.id)
        await self._save_message(conversation.id, "user", text)

        outcome = await self.run_turn(text, history)
        if self.settings.privacy_routing_enabled and outcome.privacy_scope is None:
            outcome.privacy_scope = "private"

        pending_action: PendingAction | None = None
        if outcome.status == "pending_confirmation":
            pending_action = self._confirmation(outcome, conversation.id, user_id, text)
        if outcome.blocked:
            self.db.add(
                AuditLog(actor=user_id, action="prompt_injection_blocked", resource=conversation.id)
            )
        if outcome.loop is not None:
            self._audit_tool_calls(user_id, conversation.id, outcome.loop)

        assistant_message = await self._save_message(
            conversation.id,
            "assistant",
            outcome.answer,
            [citation.model_dump() for citation in outcome.citations],
        )
        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
        return ChatResponse(
            session_id=conversation.id,
            message_id=assistant_message.id,
            status=outcome.status,
            answer=outcome.answer,
            answer_source=outcome.answer_source,
            privacy_scope=outcome.privacy_scope,
            citations=outcome.citations,
            pending_action=pending_action,
            clarification=outcome.clarification,
            retryable=outcome.retryable,
            conflicts=outcome.conflicts,
        )


async def get_conversation(db: AsyncSession, session_id: str, user_id: str) -> Conversation:
    conversation = await db.scalar(
        select(Conversation).where(Conversation.id == session_id, Conversation.user_id == user_id)
    )
    if not conversation:
        raise LookupError("会话不存在")
    return conversation
