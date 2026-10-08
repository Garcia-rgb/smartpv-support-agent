import pytest

from support_agent.services.tools import (
    ToolError,
    has_structured_anchor,
    query_device,
    safe_calculate,
)


def test_safe_calculator() -> None:
    assert safe_calculate("(12 + 8) / 4") == 5


@pytest.mark.parametrize("expression", ["__import__('os')", "2 ** 100", "1 / 0"])
def test_calculator_rejects_unsafe_or_invalid_input(expression: str) -> None:
    with pytest.raises(ToolError):
        safe_calculate(expression)


def test_query_device() -> None:
    assert query_device("sn-2024-000123").status == "并网发电"
    with pytest.raises(ToolError, match="未找到"):
        query_device("SN-2024-000999")


@pytest.mark.parametrize(
    "text",
    [
        "SN-2024-000123 这台设备现在什么状态",
        "sn-2024-000123",  # 小写写法也要认，find_device_sn 会先转大写
        "计算 100*0.986",
        "100 乘以 0.986 等于多少",
        "帮我建个工单",
        "设备有异响，我要报修",
    ],
)
def test_structured_anchor_is_detected(text: str) -> None:
    """这几类问题由工具承接，语料范围判据要豁免它们（见 agent.py 的前置拦截）。"""
    assert has_structured_anchor(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "逆变器绝缘阻抗低怎么排查",
        "Python 怎么装环境",
        "帮我看看这份资料说了什么",
        # 单独的「-」不算算式：日期与编号里到处都是，认了会把跑题问题一起放行
        "2024-09-16 的告警记录怎么看",
    ],
)
def test_plain_questions_have_no_structured_anchor(text: str) -> None:
    assert has_structured_anchor(text) is False


@pytest.mark.parametrize("text", [
    "最大直流工作电流44/44/44/44A，数据不刷新",
    "直流短路电流55/55/55/55A",
    "额定输出电压3/N/PE or 3/PE~380V",
    "2025/09/16的告警记录",
    "设备型号GW80K-MT，编号350-00396-01",
])
def test_nameplate_numbers_do_not_trigger_calculation(text):
    from support_agent.services.agent import _local_tool_request
    from support_agent.services.tools import is_calculation_request

    assert not is_calculation_request(text)
    assert not has_structured_anchor(text)
    assert not _local_tool_request(text)


@pytest.mark.parametrize("text", [
    "计算 (12+8)/4", "calc: 100/2", "100 乘以 0.986 等于多少", "12+8",
])
def test_explicit_calculation_still_has_a_tool_route(text):
    from support_agent.services.agent import _local_tool_request
    from support_agent.services.tools import is_calculation_request

    assert is_calculation_request(text)
    assert has_structured_anchor(text)
    assert _local_tool_request(text)
