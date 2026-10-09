"""没有配置远程模型时的本地替代模型。

它不做任何语言理解：用前几周写好的规则路由把用户意图映射成「申请调用某个工具」，
再用固定模板把工具结果拼成一句回答。

它存在的意义不是「答得好」，而是让 Agent Loop 在没有 API Key 的环境下
（本地演示、CI 测试）也能走完整条真实链路——工具白名单、参数校验、
写操作拦截、轮数控制，一个都不少。真模型和它在 Agent Loop 眼里没有区别，
因为它们实现的是同一个 ``chat_with_tools`` 接口。
"""

import json
from typing import Any

from ..graph import build_route_graph
from .engineering_intents import plan_engineering
from .llm import AssistantTurn, ToolCallRequest
from .modbus_tools import render_modbus, render_registers

ERROR_PREFIX = "错误："
FALLBACK_ANSWER = "我可以帮你查询设备运行状态、计算数值，或者回答产品与运维规范方面的问题。"
NO_TOOL_ANSWER = "我暂时没有可以使用的工具，没法处理这个请求。"


def _tool_names(tools: list[dict[str, Any]] | None) -> set[str]:
    """从发给模型的工具说明书里取出可用工具名。"""
    names: set[str] = set()
    for item in tools or []:
        name = item.get("function", {}).get("name")
        if isinstance(name, str):
            names.add(name)
    return names


def _last_user_message(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _last_tool_result(messages: list[dict[str, Any]]) -> tuple[str | None, str]:
    """找到最近一次工具结果，以及它属于哪个工具。"""
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") != "tool":
            continue
        output = str(messages[index].get("content") or "")
        for earlier in range(index - 1, -1, -1):
            calls = messages[earlier].get("tool_calls") or []
            name = calls[0].get("function", {}).get("name") if calls else None
            if isinstance(name, str):
                return name, output
            if calls:
                break
        return None, output
    return None, ""


class RuleBasedLocalModel:
    """规则驱动的假模型：把分类结果翻译成工具申请，把工具结果翻译成人话。"""

    def __init__(self, graph: Any = None):
        self.graph = build_route_graph() if graph is None else graph

    async def chat_with_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> AssistantTurn:
        available = _tool_names(tools)
        # tools 为空有两种情况：轮数用尽后的强制收敛，或者调用方明确不给工具。
        if not available:
            return self._finalize(messages)
        if _last_tool_result(messages)[0] is not None:
            # 已经拿到过工具结果就不再申请，保证本地模式最多两轮结束。
            return self._finalize(messages)

        text = _last_user_message(messages)
        plan = plan_engineering(text, messages[:-1])
        if plan:
            if plan.error:
                return AssistantTurn(plan.error)
            if plan.name not in available:
                return AssistantTurn(NO_TOOL_ANSWER)
            return AssistantTurn(
                "",
                [
                    ToolCallRequest(
                        "engineering_1", plan.name, json.dumps(plan.arguments, ensure_ascii=False)
                    )
                ],
            )
        state = await self.graph.ainvoke({"message": text})
        route = state["route"]
        if route == "blocked":
            return AssistantTurn("该请求可能试图绕过系统规则，我不能执行。")

        name, arguments = self._plan(route, state, text)
        if name not in available:
            return AssistantTurn(NO_TOOL_ANSWER)
        return AssistantTurn(
            "", [ToolCallRequest("local_call_1", name, json.dumps(arguments, ensure_ascii=False))]
        )

    @staticmethod
    def _plan(route: str, state: dict[str, Any], text: str) -> tuple[str, dict[str, Any]]:
        """把规则路由的输出映射成工具名和参数。"""
        if route == "calculator":
            return "calculator", {"expression": state.get("expression") or text}
        if route == "device":
            return "query_device", {"sn": state.get("device_sn") or ""}
        if route == "ticket":
            arguments: dict[str, Any] = {"reason": text}
            if state.get("device_sn"):
                arguments["device_sn"] = state["device_sn"]
            return "create_ticket", arguments
        return "search_knowledge_base", {"query": text}

    @staticmethod
    def _finalize(messages: list[dict[str, Any]]) -> AssistantTurn:
        """没有更多工具可用时，用手上的工具结果拼出最终回答。"""
        name, output = _last_tool_result(messages)
        if name is None:
            return AssistantTurn(FALLBACK_ANSWER)
        if output.startswith(ERROR_PREFIX):
            return AssistantTurn(output[len(ERROR_PREFIX) :])
        return AssistantTurn(RuleBasedLocalModel._render(name, output))

    @staticmethod
    def _render(name: str, output: str) -> str:
        """把工具返回的结构化结果拼成一句技术支持口吻的回答。"""
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            return output
        if name == "calculator" and isinstance(payload, int | float):
            return f"计算结果：{payload:g}"
        if name == "query_device" and isinstance(payload, dict):
            grid = "已并网" if payload.get("grid_connected") else "未并网"
            return (
                f"设备 {payload.get('sn')}（{payload.get('model')}）当前状态为“"
                f"{payload.get('status')}”，额定功率 "
                f"{float(payload.get('rated_power_kw', 0)):.0f} kW，{grid}，"
                f"固件版本 {payload.get('firmware')}。"
            )
        if name == "create_ticket":
            return "创建工单会产生写操作，请确认后再提交。"
        if name == "modbus_parse" and isinstance(payload, dict):
            return render_modbus(payload)
        if name == "register_decode" and isinstance(payload, dict):
            return render_registers(payload)
        # search_knowledge_base 返回的片段本身就带 [资料n] 标注，直接给出即可。
        return output
