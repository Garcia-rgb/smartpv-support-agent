from unittest.mock import AsyncMock

from support_agent.config import Settings
from support_agent.services.agent import SupportAgent, TurnOutcome, conversation_context


async def test_followup_reuses_saved_facts_and_advice_but_new_chat_does_not(db_session):
    agent = SupportAgent(db_session, Settings(privacy_routing_enabled=True))
    agent._try_quiz = AsyncMock(return_value=None)
    agent._privacy_knowledge_turn = AsyncMock(return_value=TurnOutcome(
        status="completed", answer="先核对采集器供电，再检查通信线。",
        answer_source="knowledge", privacy_scope="private",
    ))
    first = await agent.respond("华为逆变器断联怎么排查？", None, "alice")
    await agent.respond("检查了，采集器电源灯亮，还是断联", first.session_id, "alice")
    query, question = agent._privacy_knowledge_turn.await_args.args
    assert "华为逆变器断联" in query
    assert "采集器电源灯亮" in query
    assert "先核对采集器供电" not in query
    assert "先核对采集器供电" in question
    assert "不是已执行操作" in question
    assert "当前问题" in question
    await agent.respond("检查了，采集器电源灯亮", None, "alice")
    query, question = agent._privacy_knowledge_turn.await_args.args
    assert query == question == "检查了，采集器电源灯亮"


def test_explicit_new_question_and_standalone_question_do_not_reuse_context():
    history = [{"role": "user", "content": "客户甲断联"}]
    for text in ("另一个问题：绝缘阻抗低怎么办", "SUN2000绝缘阻抗低如何排查？"):
        assert conversation_context(text, history) == (text, text)


def test_quiz_explanation_followup_keeps_previous_options():
    query, question = conversation_context("为什么不选B？", [
        {"role": "user", "content": "长组串设计场景？A 满配 B 部分配"},
        {"role": "assistant", "content": "选择A"},
    ])
    assert "A 满配 B 部分配" in query
    assert "选择A" in question
