"""分库检索与远程调用的边界回归。"""

from unittest.mock import AsyncMock

from support_agent.config import Settings
from support_agent.services.agent import SupportAgent
from support_agent.services.privacy import safe_web_query
from support_agent.services.rag import RAGService


def routed_settings(**overrides):
    base = {
        "privacy_routing_enabled": True,
        "allow_remote_llm": True,
        "llm_base_url": "https://example.invalid/v1",
        "llm_api_key": "test-only",
        "llm_model": "test-model",
        "local_llm_base_url": "http://127.0.0.1:11434/v1",
        "local_llm_model": "local-test",
    }
    return Settings(**(base | overrides))


async def test_public_question_uses_only_public_chunks_and_no_private_history(db_session):
    rag = RAGService(db_session)
    await rag.ingest(
        "public-manual.md", "text/markdown",
        "SUN2000 绝缘阻抗低告警如何排查？先检查组件和接线。".encode(),
        visibility="public",
    )
    await rag.ingest(
        "private-record.md", "text/markdown",
        "内部客户秘密：现场绝缘阻抗低，联系人张三。".encode(),
    )
    agent = SupportAgent(db_session, routed_settings())
    agent.llm.answer = AsyncMock(return_value="公开资料回答")
    agent.local_llm.answer = AsyncMock(return_value="本地回答")

    first = await agent.respond("客户甲电站内部记录请帮我看", None, "u1")
    second = await agent.respond("SUN2000 绝缘阻抗低告警如何排查？", first.session_id, "u1")

    assert first.privacy_scope == "private"
    assert second.privacy_scope == "public"
    assert second.answer == "公开资料回答"
    sent_question, sent_contexts = agent.llm.answer.await_args.args
    assert sent_question == "SUN2000 绝缘阻抗低告警如何排查？"
    assert all("内部客户秘密" not in chunk for chunk in sent_contexts)
    assert all("客户甲" not in chunk for chunk in sent_contexts)


async def test_private_question_sends_current_question_and_matched_chunks(db_session):
    rag = RAGService(db_session)
    await rag.ingest(
        "private-record.md", "text/markdown",
        "客户甲电站内部记录：SUN2000 绝缘阻抗低告警，检查直流接线。".encode(),
    )
    agent = SupportAgent(db_session, routed_settings())
    agent.llm.answer = AsyncMock(return_value="远程整理回答")
    agent.local_llm.answer = AsyncMock(return_value="本地资料回答")

    result = await agent.respond("客户甲电站的SUN2000绝缘阻抗低怎么查？", None, "u1")

    assert result.privacy_scope == "private"
    assert result.answer == "远程整理回答"
    sent_question, sent_contexts = agent.llm.answer.await_args.args
    assert sent_question == "客户甲电站的SUN2000绝缘阻抗低怎么查？"
    assert any("客户甲电站内部记录" in chunk for chunk in sent_contexts)
    agent.local_llm.answer.assert_not_awaited()


async def test_public_and_private_retrieval_caches_stay_separate(db_session):
    rag = RAGService(db_session)
    await rag.ingest(
        "public.md", "text/markdown", "SUN2000 绝缘阻抗低公开说明".encode(),
        visibility="public",
    )
    await rag.ingest("private.md", "text/markdown", "SUN2000 绝缘阻抗低内部记录".encode())

    public = await rag.search("SUN2000 绝缘阻抗低", visibility="public")
    private = await rag.search("SUN2000 绝缘阻抗低", visibility="private")
    public_cached = await rag.search("SUN2000 绝缘阻抗低", visibility="public")

    assert {h.document.filename for h in public} == {"public.md"}
    assert {h.document.filename for h in private} == {"private.md"}
    assert {h.document.filename for h in public_cached} == {"public.md"}


def test_web_query_rebuilds_generic_terms_without_identifiers():
    query = safe_web_query("杭州客户张三 SN-2024-000123 的 SUN2000-100KTL-M1 绝缘阻抗低怎么处理？")
    assert query == "SUN2000-100KTL-M1 绝缘阻抗低 绝缘阻抗"
    assert safe_web_query("客户张三住在哪里？") is None


def test_local_endpoint_must_be_loopback():
    assert routed_settings().local_llm_enabled
    assert not routed_settings(local_llm_base_url="https://external.invalid/v1").local_llm_enabled


async def test_private_miss_searches_only_sanitized_public_terms(db_session, monkeypatch):
    search = AsyncMock(return_value=[{
        "title": "公开手册", "url": "https://example.org/manual", "description": "绝缘阻抗说明",
    }])
    monkeypatch.setattr("support_agent.services.agent.search_public_web", search)
    agent = SupportAgent(db_session, routed_settings(public_search_api_key="test-only"))
    agent.llm.answer = AsyncMock(return_value="请核对设备手册")
    agent.local_llm.answer = AsyncMock(return_value="请核对设备手册")

    result = await agent.respond(
        "客户甲 SN-2024-000123 的 SUN2000-100KTL-M1 绝缘阻抗低怎么办？",
        None, "u1",
    )

    assert result.privacy_scope == "private"
    assert "公开检索来源" in result.answer
    assert search.await_args.args[0] == "SUN2000-100KTL-M1 绝缘阻抗低 绝缘阻抗"
    assert agent.llm.answer.await_count == 1
    agent.local_llm.answer.assert_not_awaited()
