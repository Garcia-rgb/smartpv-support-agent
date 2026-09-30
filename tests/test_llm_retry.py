from unittest.mock import AsyncMock, patch

import httpx
import pytest

from support_agent.config import Settings
from support_agent.services.llm import AssistantTurn, LLMError, OpenAICompatibleClient


def remote_settings() -> Settings:
    return Settings(
        llm_base_url="https://llm.test/v1",
        llm_api_key="test-key",
        llm_model="test-model",
        allow_remote_llm=True,
    )


def response(status_code: int, json: dict | None = None) -> httpx.Response:
    request = httpx.Request("POST", "https://llm.test/v1/chat/completions")
    if json is None:
        return httpx.Response(status_code, request=request)
    return httpx.Response(status_code, request=request, json=json)


def successful_response(answer: str = "模型回答") -> httpx.Response:
    return response(200, {"choices": [{"message": {"content": answer}}]})


async def test_authentication_error_is_not_retried() -> None:
    with (
        patch("support_agent.services.llm.httpx.AsyncClient") as client_class,
        patch("support_agent.services.llm.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        post = client_class.return_value.__aenter__.return_value.post
        post.return_value = response(401)

        with pytest.raises(LLMError) as captured:
            await OpenAICompatibleClient(remote_settings()).answer("问题", [])

    assert captured.value.category == "authentication"
    assert captured.value.retryable is False
    assert post.await_count == 1
    sleep.assert_not_awaited()


@pytest.mark.parametrize(
    ("status_code", "category"),
    [(429, "rate_limit"), (503, "service")],
)
async def test_retryable_http_error_uses_three_attempts(
    status_code: int, category: str
) -> None:
    with (
        patch("support_agent.services.llm.httpx.AsyncClient") as client_class,
        patch("support_agent.services.llm.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        post = client_class.return_value.__aenter__.return_value.post
        post.return_value = response(status_code)

        with pytest.raises(LLMError) as captured:
            await OpenAICompatibleClient(remote_settings()).answer("问题", [])

    assert captured.value.category == category
    assert captured.value.retryable is True
    assert post.await_count == 3
    assert sleep.await_count == 2


async def test_timeout_is_retried_and_can_recover() -> None:
    request = httpx.Request("POST", "https://llm.test/v1/chat/completions")
    with (
        patch("support_agent.services.llm.httpx.AsyncClient") as client_class,
        patch("support_agent.services.llm.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        post = client_class.return_value.__aenter__.return_value.post
        post.side_effect = [httpx.ReadTimeout("读取超时", request=request), successful_response()]

        answer = await OpenAICompatibleClient(remote_settings()).answer("问题", [])

    assert answer == "模型回答"
    assert post.await_count == 2
    sleep.assert_awaited_once_with(1)


async def test_invalid_response_is_not_retried() -> None:
    with (
        patch("support_agent.services.llm.httpx.AsyncClient") as client_class,
        patch("support_agent.services.llm.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        post = client_class.return_value.__aenter__.return_value.post
        post.return_value = response(200, {"unexpected": "shape"})

        with pytest.raises(LLMError) as captured:
            await OpenAICompatibleClient(remote_settings()).answer("问题", [])

    assert captured.value.category == "invalid_response"
    assert captured.value.retryable is False
    assert post.await_count == 1
    sleep.assert_not_awaited()


def tool_call_message(**extra: object) -> dict:
    """构造一条「模型要求调工具」的响应，extra 用于附加厂商扩展字段。"""
    arguments = '{"expression": "1+1"}'
    return {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "calculator", "arguments": arguments},
                        }
                    ],
                    **extra,
                }
            }
        ]
    }


async def test_reasoning_content_is_kept_for_the_next_round() -> None:
    """思考模式下思维链必须跟着 assistant 消息回传，否则续轮会被拒。"""
    with patch("support_agent.services.llm.httpx.AsyncClient") as client_class:
        post = client_class.return_value.__aenter__.return_value.post
        post.return_value = response(200, tool_call_message(reasoning_content="先算 1+1"))

        turn = await OpenAICompatibleClient(remote_settings()).chat_with_tools(
            [{"role": "user", "content": "1+1"}],
            tools=[{"type": "function", "function": {"name": "calculator"}}],
        )

    assert turn.reasoning_content == "先算 1+1"
    message = turn.to_message()
    assert message["reasoning_content"] == "先算 1+1"
    assert message["tool_calls"][0]["function"]["name"] == "calculator"


async def test_reasoning_content_is_absent_when_the_model_does_not_send_it() -> None:
    """非思考模式的模型不带这个字段，回传的消息里也不应该凭空多出它。"""
    with patch("support_agent.services.llm.httpx.AsyncClient") as client_class:
        post = client_class.return_value.__aenter__.return_value.post
        post.return_value = response(200, tool_call_message())

        turn = await OpenAICompatibleClient(remote_settings()).chat_with_tools(
            [{"role": "user", "content": "1+1"}],
            tools=[{"type": "function", "function": {"name": "calculator"}}],
        )

    assert turn.reasoning_content is None
    assert "reasoning_content" not in turn.to_message()


def test_reasoning_content_with_a_wrong_type_is_ignored() -> None:
    """厂商扩展字段格式异常时只当没有，不影响必需字段的校验。"""
    turn = AssistantTurn.from_message({"content": "好的", "reasoning_content": {"text": "x"}})

    assert turn.content == "好的"
    assert turn.reasoning_content is None
    assert "reasoning_content" not in turn.to_message()
