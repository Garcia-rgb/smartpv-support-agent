from types import SimpleNamespace
from unittest.mock import AsyncMock

from support_agent.config import Settings
from support_agent.services.evidence import (
    failed_actions,
    guard_failed_recommendations,
    prepare_evidence_context,
    usable_case_text,
)
from support_agent.services.llm import OpenAICompatibleClient
from support_agent.services.local_summary import summarize_explicit_case, summarize_local_retrieval

FAILED = "- 数采挂不上\n尝试恢复出厂设置并静待，无效。问题还在调试中。"


def test_failed_outcome_is_preserved_before_context_truncation():
    content = "尝试恢复出厂。" + "现场记录" * 500 + "无效，问题还在调试中。"
    context = prepare_evidence_context(content, 1200)
    assert len(context) <= 1200
    assert "恢复出厂" in failed_actions(context)
    assert "失败尝试" in context


def test_unresolved_case_does_not_hide_separate_successful_case():
    content = FAILED + "\n- 无直流数值\n解决方法：配置组串容量后数据恢复正常。"
    usable = usable_case_text(content)
    assert "恢复出厂" not in usable
    assert "配置组串容量后数据恢复正常" in usable


def test_server_filters_failed_action_even_if_model_recommends_it():
    answer = guard_failed_recommendations("先核对485通信。必要时恢复出厂设置。", [FAILED])
    assert "先核对485通信" in answer
    assert "必要时恢复出厂" not in answer
    assert "不能作为已验证" in answer
    warning = "资料记录恢复出厂无效，不建议恢复出厂。"
    assert guard_failed_recommendations(warning, [FAILED]) == warning


def test_local_summary_never_calls_failed_solution_effective():
    content = "逆变器挂不上\n解决方法：恢复出厂。\n实际无效，问题未解决。"
    hits = [SimpleNamespace(chunk=SimpleNamespace(content=content))]
    assert summarize_explicit_case("逆变器挂不上", hits) is None
    answer = summarize_local_retrieval("逆变器挂不上怎么排查", hits)
    assert "不能" in answer or "没有可确认" in answer
    assert "排查要点" not in answer


async def test_real_llm_adapter_tags_context_and_guards_its_output():
    settings = Settings(allow_remote_llm=True, llm_base_url="https://example.invalid",
                        llm_model="test", llm_api_key="test-only")
    client = OpenAICompatibleClient(settings)
    client._chat = AsyncMock(return_value={"choices": [{"message": {
        "content": "先检查通信。然后恢复出厂设置。"
    }}]})
    answer = await client.answer("逆变器挂不上", [FAILED])
    payload = client._chat.await_args.args[0]
    assert "失败尝试" in payload["messages"][1]["content"]
    assert "不得推荐" in payload["messages"][0]["content"]
    assert "然后恢复出厂" not in answer
    assert "先检查通信" in answer
