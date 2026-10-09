from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from support_agent.config import Settings
from support_agent.models import AuditLog, Message
from support_agent.services.agent import SupportAgent, TurnOutcome, conversation_context
from support_agent.services.business_skills import instructions, issue_state, select_skill
from support_agent.services.engineering_intents import plan_engineering
from support_agent.services.llm import OpenAICompatibleClient


def test_skill_selection_and_trusted_files_only():
    assert select_skill("帮我制作北向点表").identifier == "point_table"
    assert select_skill("逆变器还是断联").identifier == "site_troubleshooting"
    assert select_skill("今天学习单选题") is None
    assert "point-tables/validate" in instructions("point_table")
    assert "助手建议不等于用户已执行" in instructions("site_troubleshooting")
    with pytest.raises(ValueError):
        instructions("../../uploaded/SKILL")


def test_context_ledger_never_promotes_assistant_advice_to_execution():
    history = [
        {"role": "user", "content": "GW80K-MT数据不刷新"},
        {"role": "assistant", "content": "建议恢复出厂"},
        {"role": "user", "content": "检查了，采集器电源灯亮，重启无效"},
    ]
    state = issue_state("接下来怎么查？", history)
    assert "GW80K-MT" in state and "重启无效" in state and "电源灯亮" in state
    assert "建议恢复出厂" not in state
    assert issue_state("新问题：电表离线", history) == ""
    query, question = conversation_context("接下来怎么查？", history)
    assert "当前问题状态" in question and "已反馈" not in query


def test_register_order_followup_and_ambiguous_gain():
    first = "寄存器解码：[0x43C8,0x0000]，FLOAT32，ABCD"
    plan = plan_engineering("换成CDAB呢", [{"role": "user", "content": first}])
    assert plan.arguments["word_order"] == "low_first"
    assert plan.arguments["registers"] == [0x43C8, 0]
    assert plan_engineering("换成CDAB呢") is None
    assert plan_engineering(first + "；增益10").error
    plan = plan_engineering(first + "；除数10")
    assert plan.arguments["multiplier"] == 0.1


def test_frame_followup_and_invalid_suffix_are_not_silently_trimmed():
    first = "解析Modbus RTU请求报文：01 03 00 00 00 02 C4 0B"
    plan = plan_engineering("这是TCP响应报文", [{"role": "user", "content": first}])
    assert plan.arguments["transport"] == "tcp" and plan.arguments["direction"] == "response"
    malformed = plan_engineering(first + " ZZ")
    assert malformed.arguments["frame"].endswith("ZZ")


async def test_chat_executes_tools_locally_and_preserves_history_and_audit(db_session):
    agent = SupportAgent(db_session, Settings(privacy_routing_enabled=True))
    agent.llm.answer = AsyncMock(side_effect=AssertionError("工具不应调用远程模型"))
    first = await agent.respond("解析Modbus RTU请求报文：01 03 00 00 00 02 C4 0B", None, "alice")
    assert first.answer_source == "tool" and "CRC：通过" in first.answer
    decoded = await agent.respond(
        "寄存器解码：值=0x43C8,0x0000；类型=FLOAT32；字序=high_first；乘数=0.1",
        first.session_id,
        "alice",
    )
    assert decoded.answer_source == "tool" and "换算后 40.0" in decoded.answer
    again = await agent.respond("换成CDAB呢", first.session_id, "alice")
    assert "低字在前" in again.answer
    messages = (
        await db_session.scalars(select(Message).where(Message.session_id == first.session_id))
    ).all()
    assert len(messages) == 6
    logs = (
        await db_session.scalars(select(AuditLog).where(AuditLog.resource == first.session_id))
    ).all()
    assert len(logs) == 3
    with pytest.raises(PermissionError):
        await agent.respond("换成ABCD", first.session_id, "bob")


async def test_point_skill_dispatches_existing_editor_and_missing_tool_parameters(db_session):
    agent = SupportAgent(db_session, Settings(privacy_routing_enabled=True))
    result = await agent.respond("帮我生成北向点表", None, "alice")
    assert result.next_action == "point_table" and result.skill_id == "point_table"
    assert result.answer.startswith("点表制作：北向")
    missing = await agent.respond("帮我解析Modbus报文", None, "alice")
    assert missing.status == "needs_clarification" and missing.next_action == "modbus_parse"


async def test_troubleshooting_skill_is_in_actual_model_system_prompt():
    model = OpenAICompatibleClient(
        Settings(
            allow_remote_llm=True,
            llm_api_key="test-only",
            llm_base_url="https://example.invalid/v1",
            llm_model="test-only",
        )
    )
    model._chat = AsyncMock(
        return_value={"choices": [{"message": {"content": "先核对采集器状态。"}}]}
    )
    await model.answer("GW80K-MT还是断联，检查了采集器电源正常", ["检查采集器供电与通信线。"])
    payload = model._chat.await_args.args[0]
    assert "现场排查" in payload["messages"][0]["content"]
    assert "助手建议不等于用户已执行" in payload["messages"][0]["content"]


async def test_chat_api_routes_engineering_request_and_returns_action(client):
    response = await client.post("/chat", json={"message": "寄存器解码：值=65535；类型=INT16"})
    assert response.status_code == 200
    assert response.json()["answer_source"] == "tool"
    assert "原值 -1" in response.json()["answer"]
    response = await client.post("/chat", json={"message": "请制作南向点表"})
    assert response.json()["next_action"] == "point_table"


async def test_long_chat_retains_current_topic_anchor_without_old_fault(db_session):
    agent = SupportAgent(db_session, Settings(chat_history_limit=4))
    conversation = await agent._conversation(None, "alice")
    for text in [
        "SUN2000断联",
        "新问题：GW80K-MT数据不刷新",
        "检查了电源正常",
        "检查了485线正常",
        "重启没有恢复",
        "数据时间还是昨天",
    ]:
        await agent._save_message(conversation.id, "user", text)
    history = await agent._history(conversation.id)
    ledger = issue_state("下一步查哪里？", history)
    assert "GW80K-MT" in ledger and "SUN2000" not in ledger


async def test_tool_then_new_fault_starts_factual_context_at_fault(db_session):
    agent = SupportAgent(db_session, Settings(privacy_routing_enabled=True, chat_history_limit=4))
    first = await agent.respond("解析Modbus RTU报文：01 03 00 00 00 02 C4 0B", None, "alice")
    agent._privacy_knowledge_turn = AsyncMock(
        return_value=TurnOutcome(
            status="completed", answer="核对通信链路", answer_source="knowledge"
        )
    )
    await agent.respond("GW80K-MT数据不刷新", first.session_id, "alice")
    for text in ["检查了电源正常", "检查了485线正常", "重启无效", "数据时间还是昨天"]:
        await agent.respond(text, first.session_id, "alice")
    query, question = agent._privacy_knowledge_turn.await_args.args
    assert "GW80K-MT" in query and "GW80K-MT" in question
    assert "C4 0B" not in query and "C4 0B" not in question
