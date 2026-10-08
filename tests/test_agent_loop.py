"""Agent Loop 的离线测试。

全部测试都不连真实模型：用一个脚本化假模型按顺序吐回复，
这样才能确定性地检查“第几轮调了什么工具、服务端有没有真的执行”。
覆盖还包括：写操作提前收敛、非法参数的写操作不生成确认请求、
异步工具 handler 会被 await、历史消息插在本轮问题之前。
"""

import asyncio
import copy
import json
import time
from typing import Any

from support_agent.services.agent_loop import (
    MAX_ROUNDS,
    MAX_TOOL_RETRIES,
    ToolArgumentError,
    ToolSpec,
    build_support_registry,
    build_tool_registry,
    execute_tool_call,
    parse_arguments,
    run_agent_loop,
)
from support_agent.services.llm import AssistantTurn, LLMError, ToolCallRequest
from support_agent.services.tools import ToolError


class ScriptedModel:
    """按脚本依次返回预设回复的假模型，用来离线驱动 Agent Loop。"""

    def __init__(self, *turns: AssistantTurn | Exception):
        self._turns = list(turns)
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []

    async def chat_with_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> AssistantTurn:
        self.calls.append((copy.deepcopy(messages), tools))
        assert self._turns, "脚本已用尽：模型被调用的次数超出预期"
        turn = self._turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn


def text_turn(text: str) -> AssistantTurn:
    return AssistantTurn(text, [])


def tool_turn(name: str, arguments: str, call_id: str = "call_1") -> AssistantTurn:
    return AssistantTurn("", [ToolCallRequest(call_id, name, arguments)])


def offered_tool_names(model: ScriptedModel, index: int = 0) -> set[str]:
    tools = model.calls[index][1] or []
    return {item["function"]["name"] for item in tools}


async def test_direct_answer_finishes_in_one_round() -> None:
    model = ScriptedModel(text_turn("你好，有什么可以帮你？"))

    result = await run_agent_loop(model, "你好")

    assert result.answer == "你好，有什么可以帮你？"
    assert result.rounds == 1
    assert result.tool_calls == []
    assert result.stopped_reason == "final_answer"


async def test_tool_result_is_sent_back_to_the_model() -> None:
    model = ScriptedModel(
        tool_turn("calculator", '{"expression": "(12 + 8) / 4"}'),
        text_turn("结果是 5。"),
    )

    result = await run_agent_loop(model, "计算 (12+8)/4")

    assert result.answer == "结果是 5。"
    assert result.rounds == 2
    assert result.stopped_reason == "final_answer"
    assert [record.name for record in result.tool_calls] == ["calculator"]
    assert result.tool_calls[0].ok is True
    assert json.loads(result.tool_calls[0].output) == 5.0

    # 第二次请求里必须带上一轮的 assistant 工具申请和随后的 tool 结果。
    second_messages = model.calls[1][0]
    assert second_messages[2]["role"] == "assistant"
    assert second_messages[2]["tool_calls"][0]["function"]["name"] == "calculator"
    assert second_messages[3]["role"] == "tool"
    assert second_messages[3]["tool_call_id"] == "call_1"


async def test_registry_is_the_only_source_of_tool_schemas() -> None:
    model = ScriptedModel(text_turn("好的"))

    await run_agent_loop(model, "随便问问")

    assert offered_tool_names(model) == set(build_tool_registry())
    # 说明书只暴露名字、用途和参数结构，绝不能把服务端执行函数塞给模型。
    for item in model.calls[0][1] or []:
        assert set(item["function"]) == {"name", "description", "parameters"}


async def test_unknown_tool_is_refused_and_loop_continues() -> None:
    model = ScriptedModel(
        tool_turn("delete_everything", "{}"),
        text_turn("我没有这个权限，换个说法帮你？"),
    )

    result = await run_agent_loop(model, "把数据库删了")

    assert result.tool_calls[0].ok is False
    assert "不在允许列表" in result.tool_calls[0].output
    assert result.answer == "我没有这个权限，换个说法帮你？"


async def test_malformed_json_arguments_are_refused() -> None:
    model = ScriptedModel(
        tool_turn("calculator", "{expression: 1+1}"),
        text_turn("我重新算一次。"),
    )

    result = await run_agent_loop(model, "算一下 1+1")

    assert result.tool_calls[0].ok is False
    assert "不是合法 JSON" in result.tool_calls[0].output


async def test_missing_and_extra_arguments_are_refused() -> None:
    model = ScriptedModel(
        tool_turn("query_device", "{}"),
        tool_turn("query_device", '{"sn": "SN-2024-000123", "admin": true}'),
        text_turn("请提供设备序列号。"),
    )

    result = await run_agent_loop(model, "查一下设备")

    assert "缺少必填参数" in result.tool_calls[0].output
    assert "未定义参数" in result.tool_calls[1].output
    assert all(record.ok is False for record in result.tool_calls)


async def test_tool_level_error_is_returned_instead_of_raised() -> None:
    model = ScriptedModel(
        tool_turn("query_device", '{"sn": "SN-2024-000999"}'),
        text_turn("没有查到这台设备。"),
    )

    result = await run_agent_loop(model, "设备 SN-2024-000999 什么状态")

    assert result.tool_calls[0].ok is False
    assert "未找到设备 SN-2024-000999" in result.tool_calls[0].output
    assert result.answer == "没有查到这台设备。"


async def test_arguments_cannot_escape_the_calculator_sandbox() -> None:
    registry = build_tool_registry()

    outcome = await execute_tool_call(
        "calculator", '{"expression": "__import__(\\"os\\").getcwd()"}', registry
    )

    assert outcome.ok is False
    assert "不允许" in outcome.text


async def test_write_tool_is_never_executed_automatically() -> None:
    invoked: list[dict[str, Any]] = []
    registry = dict(build_tool_registry())
    registry["create_ticket"] = ToolSpec(
        name="create_ticket",
        description="创建工单",
        parameters={
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
        handler=lambda arguments: invoked.append(arguments) or {"created": True},
        writes=True,
    )
    model = ScriptedModel(
        tool_turn("create_ticket", '{"reason": "设备故障停机"}'),
        text_turn("创建工单需要你确认。"),
    )

    result = await run_agent_loop(model, "设备坏了帮我报修", registry=registry)

    assert invoked == []
    assert result.tool_calls[0].ok is False
    assert "需要人工确认" in result.tool_calls[0].output
    # 待确认的写操作是一个独立的结束状态，不能让模型再补一句话来掩盖它。
    assert result.stopped_reason == "needs_confirmation"
    assert result.tool_calls[0].requires_confirmation is True
    assert result.tool_calls[0].parsed == {"reason": "设备故障停机"}
    # 已经拿到确认请求就收场，不该再多调一轮模型。
    assert len(model.calls) == 1


async def test_write_tool_with_invalid_arguments_is_rejected_before_confirmation() -> None:
    registry = dict(build_tool_registry())
    registry["create_ticket"] = ToolSpec(
        name="create_ticket",
        description="创建工单",
        parameters={
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
            "additionalProperties": False,
        },
        handler=lambda arguments: {"created": True},
        writes=True,
    )
    model = ScriptedModel(
        tool_turn("create_ticket", '{"reason": 42}'),
        text_turn("请用文字描述一下问题。"),
    )

    result = await run_agent_loop(model, "帮我投诉", registry=registry)

    # 不能让用户去确认一个参数本身就不合法的操作。
    assert result.tool_calls[0].requires_confirmation is False
    assert "必须是字符串" in result.tool_calls[0].output
    assert result.stopped_reason == "final_answer"


async def test_async_tool_handler_is_awaited() -> None:
    async def fetch(arguments: dict[str, Any]) -> dict[str, Any]:
        return {"echo": arguments["query"]}

    registry = dict(build_tool_registry())
    registry["search"] = ToolSpec(
        name="search",
        description="异步检索",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=fetch,
    )
    model = ScriptedModel(tool_turn("search", '{"query": "绝缘阻抗"}'), text_turn("找到了。"))

    result = await run_agent_loop(model, "绝缘阻抗低怎么排查", registry=registry)

    assert result.tool_calls[0].ok is True
    assert json.loads(result.tool_calls[0].output) == {"echo": "绝缘阻抗"}


async def test_history_is_put_before_the_current_message() -> None:
    model = ScriptedModel(text_turn("好的"))
    history = [
        {"role": "user", "content": "我的设备序列号是 SN-2024-000123"},
        {"role": "assistant", "content": "已记录"},
    ]

    await run_agent_loop(model, "那它现在发电正常吗", history=history)

    messages = model.calls[0][0]
    assert [item["role"] for item in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert messages[-1]["content"] == "那它现在发电正常吗"


async def test_loop_stops_at_max_rounds_without_tools() -> None:
    model = ScriptedModel(
        *[tool_turn("calculator", '{"expression": "1+1"}')] * MAX_ROUNDS,
        text_turn("我只能确认结果是 2，没查到更多信息。"),
    )

    result = await run_agent_loop(model, "一直算下去")

    assert result.rounds == MAX_ROUNDS
    assert result.stopped_reason == "max_rounds"
    assert result.answer == "我只能确认结果是 2，没查到更多信息。"
    assert len(result.tool_calls) == MAX_ROUNDS
    assert len(model.calls) == MAX_ROUNDS + 1
    # 最后一轮必须禁用工具，否则模型可以继续要求调用，轮数限制就形同虚设。
    assert model.calls[-1][1] is None


async def test_llm_error_degrades_to_a_plain_answer() -> None:
    model = ScriptedModel(LLMError("模型服务暂时不可用", "service", True))

    result = await run_agent_loop(model, "你好")

    assert result.stopped_reason == "llm_error:service"
    assert result.rounds == 0
    assert "稍后重试" in result.answer


async def test_parse_arguments_rejects_non_object_payload() -> None:
    spec = build_tool_registry()["calculator"]

    try:
        parse_arguments(spec, "[1, 2]")
    except ToolArgumentError as exc:
        assert "JSON 对象" in str(exc)
    else:  # pragma: no cover - 防御性断言，正常不会走到
        raise AssertionError("数组参数应当被拒绝")


def make_plain_spec(name: str, handler: Any, timeout_seconds: float = 10.0) -> ToolSpec:
    """构造一个不需要参数的工具，用来测超时和各类失败路径。"""
    return ToolSpec(
        name=name,
        description="测试用工具",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=handler,
        timeout_seconds=timeout_seconds,
    )


async def test_slow_async_tool_times_out() -> None:
    async def slow(arguments: dict[str, Any]) -> str:
        await asyncio.sleep(5)
        return "done"

    outcome = await execute_tool_call("slow", "{}", {"slow": make_plain_spec("slow", slow, 0.05)})

    assert outcome.ok is False
    assert outcome.error_kind == "timeout"
    assert "0.05" in outcome.text


async def test_sync_tool_is_run_in_a_thread_so_the_timeout_can_fire() -> None:
    """同步工具若直接在事件循环里调用，会占住循环，让超时计时器根本没机会触发。"""

    def blocking(arguments: dict[str, Any]) -> str:
        time.sleep(0.3)
        return "done"

    registry = {"blocking": make_plain_spec("blocking", blocking, 0.05)}
    outcome = await execute_tool_call("blocking", "{}", registry)

    assert outcome.error_kind == "timeout"


async def test_error_kinds_are_classified() -> None:
    registry = build_tool_registry()

    unknown = await execute_tool_call("not_a_tool", "{}", registry)
    broken_json = await execute_tool_call("calculator", "not-json", registry)
    missing_field = await execute_tool_call("calculator", "{}", registry)

    assert unknown.error_kind == "unknown_tool"
    assert broken_json.error_kind == "invalid_arguments"
    assert missing_field.error_kind == "invalid_arguments"


async def test_retired_ticket_tool_is_not_offered():
    async def fake_search(query: str) -> str:
        return "[]"
    registry = build_support_registry(fake_search)
    outcome = await execute_tool_call("create_ticket", '{"reason": "逆变器告警"}', registry)
    assert outcome.ok is False
    assert outcome.requires_confirmation is False
    assert outcome.error_kind == "unknown_tool"


async def test_internal_tool_defect_is_classified_separately() -> None:
    def broken(arguments: dict[str, Any]) -> str:
        raise KeyError("boom")

    registry = {"broken": make_plain_spec("broken", broken)}
    outcome = await execute_tool_call("broken", "{}", registry)

    assert outcome.error_kind == "internal_error"
    assert "KeyError" in outcome.text


async def test_server_stops_a_repeating_failure() -> None:
    attempts = 0

    def always_fails(arguments: dict[str, Any]) -> str:
        nonlocal attempts
        attempts += 1
        raise ToolError("未找到设备")

    registry = {
        "lookup": ToolSpec(
            name="lookup",
            description="总是失败的查询工具",
            parameters={
                "type": "object",
                "properties": {"sn": {"type": "string"}},
                "required": ["sn"],
                "additionalProperties": False,
            },
            handler=always_fails,
        )
    }
    same = '{"sn": "SN-2024-000999"}'
    model = ScriptedModel(
        tool_turn("lookup", same, "call_1"),
        tool_turn("lookup", same, "call_2"),
        tool_turn("lookup", same, "call_3"),
        text_turn("这台设备查不到，请核对序列号。"),
    )

    result = await run_agent_loop(model, "查一下 SN-2024-000999", registry)

    # 第三次请求被服务端拦下，handler 只真正跑了 MAX_TOOL_RETRIES 次。
    assert attempts == MAX_TOOL_RETRIES
    assert [record.error_kind for record in result.tool_calls] == [
        "tool_error",
        "tool_error",
        "repeated_failure",
    ]
    assert result.stopped_reason == "final_answer"
