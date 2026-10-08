from collections import Counter
from unittest.mock import AsyncMock

from support_agent.config import Settings
from support_agent.models import DocumentChunk, SourceDocument
from support_agent.services.agent import SupportAgent, _dedupe_hits
from support_agent.services.rag import RAGService, SearchHit
from support_agent.services.retrieval_quality import (
    applicability_note,
    fallback_query,
    identity_adjustment,
    query_variants,
)


def hit(identifier, document, content, score):
    return SearchHit(
        DocumentChunk(id=identifier, document_id=document, position=0, content=content),
        SourceDocument(
            id=document, filename=document + ".md", content_type="text/markdown", checksum=document
        ),
        score,
    )


def test_unknown_model_fallback_keeps_brand_and_symptom_and_ignores_only_channel_alias():
    q = fallback_query("固德威 GW80K-MT 数据不更新，PV5无值", Counter({"数据": 2}))
    assert "固德威" in q and "数据不更新" in q
    assert "GW80K" not in q and "PV5" not in q
    assert fallback_query("Python虚拟环境怎么安装", {}) == "Python虚拟环境怎么安装"
    assert "SUN2000" in fallback_query("SUN2000-100KTL-M2断联", {"sun2000": 1})


def test_model_and_manufacturer_rank_and_limit_applicability():
    query = "固德威 GW80K-MT 数据不更新"
    right = "固德威 GW80K-MT 数据不更新，检查通信"
    wrong = "华为 SUN2000-100KTL-M2 数据不更新，检查通信"
    assert identity_adjustment(query, right) > identity_adjustment(query, wrong)
    assert "不得认定型号专用参数" in applicability_note(query, wrong)
    assert applicability_note(query, right) == ""
    assert len(query_variants(query)) == 2


def test_duplicate_candidates_keep_best_score_and_do_not_waste_evidence_slots():
    result = _dedupe_hits(
        [
            hit("a", "one", "重复正文", 0.5),
            hit("a", "one", "重复正文", 0.9),
            hit("b", "two", "重复正文", 0.8),
            hit("c", "three", "另一个实际依据", 0.7),
        ],
        5,
    )
    assert [h.chunk.id for h in result] == ["a", "c"]
    assert result[0].score == 0.9


def test_evidence_budget_and_failed_action_tags_survive_with_model_limit():
    hits = [hit(str(i), str(i), "恢复出厂失败；" + "普通资料" * 900, 0.8) for i in range(5)]
    selected = SupportAgent.evidence_hits(hits)
    contexts = SupportAgent.evidence_contexts("GW80K-MT数据不刷新", selected)
    assert len(selected) == len(contexts) == 4
    assert sum(map(len, contexts)) <= 6000
    assert all("不可作为解决步骤的操作：恢复出厂" in c for c in contexts)
    assert all("未明确覆盖所问完整型号" in c for c in contexts)


async def test_original_question_is_preserved_and_citations_match_supplied_evidence(db_session):
    rag = RAGService(db_session)
    await rag.ingest(
        "故障资料.md",
        "text/markdown",
        "固德威逆变器设备通信中断，检查采集器供电及485接线。".encode(),
    )
    agent = SupportAgent(
        db_session,
        Settings(
            privacy_routing_enabled=True,
            allow_remote_llm=True,
            llm_api_key="test-only",
            llm_base_url="https://example.invalid/v1",
            llm_model="test-only",
        ),
    )
    agent.llm.answer = AsyncMock(return_value="根据资料核对通信链路。")
    question = "GW80K-MT 数据不刷新怎么办？"
    result = await agent.respond(question, None, "owner")
    sent_question, contexts = agent.llm.answer.await_args.args
    assert question == sent_question
    assert len(result.citations) == len(contexts)
    assert "未明确覆盖所问完整型号" in contexts[0]
