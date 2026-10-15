from hashlib import sha256

from sqlalchemy.ext.asyncio import AsyncSession

from support_agent.config import get_settings
from support_agent.models import DocumentChunk, SourceDocument
from support_agent.services.cache import get_retrieval_cache
from support_agent.services.embeddings import local_embedding
from support_agent.services.industry import build_chunk_metadata
from support_agent.services.rag import (
    RAGService,
    SearchHit,
    has_unknown_foreign_token,
)


async def _add_chunk(
    db: AsyncSession,
    filename: str,
    content: str,
    metadata: dict,
    *,
    position: int = 0,
) -> SourceDocument:
    """直接落一条带行业元数据的片段，用来验证检索加权。

    `RAGService.ingest` 走的是上传路径，不给行业标签；分卷导入才会带，
    所以这里自己写库，避免为了测加权去准备一整套分卷目录。

    末尾必须通知缓存失效：这里是绕过 `RAGService` 直接写库，不发这个通知，
    紧接着的检索会吃到「建数据之前」的缓存。真实导入路径会自己换代次。
    """
    digest = sha256(f"{filename}:{position}".encode()).hexdigest()
    document = SourceDocument(filename=filename, content_type="text/markdown", checksum=digest)
    db.add(document)
    await db.flush()
    db.add(
        DocumentChunk(
            document_id=document.id,
            position=position,
            content=content,
            chunk_metadata=metadata,
            embedding=local_embedding(content),
        )
    )
    await db.commit()
    await get_retrieval_cache().invalidate()
    return document


async def test_ingest_deduplicate_and_search(db_session: AsyncSession) -> None:
    rag = RAGService(db_session, chunk_size=40, overlap=5)
    data = "绝缘阻抗低对应告警 2062，需检查直流侧对地绝缘与组件接线。".encode()
    document, count, duplicate = await rag.ingest("alarm.md", "text/markdown", data)
    assert count > 0
    assert duplicate is False

    same_document, same_count, duplicate = await rag.ingest("alarm.md", "text/markdown", data)
    assert same_document.id == document.id
    assert same_count == count
    assert duplicate is True

    hits = await rag.search("绝缘阻抗低对应哪个告警")
    assert hits
    assert "2062" in hits[0].chunk.content

    assert await rag.search("完全无关的问题", min_score=0.99) == []


async def test_colloquial_query_reaches_written_answer(db_session: AsyncSession) -> None:
    """口语问法要能召回书面语写的答案。

    用户不会照着文档标题提问：文档写「合母（合闸母线）」，用户问「啥是合母」。
    逐字切分下「合母」「控母」共享「母」，加上「啥」「是」这些语气字摊薄权重，
    口语问句会被无关段落挤下去，模型自然拿不到原文。
    """
    rag = RAGService(db_session, chunk_size=120, overlap=20)
    data = (
        "第四章 直流输出：合母与控母。"
        "合母是直流屏输出的合闸母线，供断路器合闸冲击电流。"
        "控母经降压硅链稳压后供控制保护回路，本项目电压 220V。"
    ).encode()
    await rag.ingest("dc-panel.md", "text/markdown", data)

    # 书面语问法
    written = await rag.search("合母和控母有什么区别", 3)
    assert written
    assert "合闸母线" in written[0].chunk.content

    # 口语问法：同一个问题，换成大白话也必须能问到
    for question in ("啥是合母", "控母是啥意思", "合母控母有啥不一样"):
        hits = await rag.search(question, 3)
        assert hits, question
        assert "合母" in "".join(hit.chunk.content for hit in hits), question

    # 库外噪声的方向性：口语问法要明显比无关问题得分高，
    # 否则最低分阈值无法把「没有依据」和「有依据」分开。
    colloquial = await rag.search("啥是合母", 3)
    noise = await rag.search("今天中午吃什么", 3)
    noise_score = noise[0].score if noise else 0.0
    assert colloquial[0].score > noise_score


async def test_metadata_facets_reorder_same_text(db_session: AsyncSession) -> None:
    """两份文字几乎一样的片段，用户点名场景时该场景的要排前面。

    这是行业元数据存在的理由：光伏资料里「户用」「工商业」「地面电站」讲同一件事
    用的词高度重合，纯文本相似度分不出该用哪一份，只有标签能分。
    """
    rag = RAGService(db_session)
    body = "组网方式包括三相四线、单相、三相三线等，需按容量和并网点类型选择。"
    await _add_chunk(
        db_session,
        "household.md",
        body,
        {"corpus_id": "demo", "scenario": "户用", "device_models": ["SUN2000"]},
    )
    await _add_chunk(
        db_session,
        "ground.md",
        body,
        {"corpus_id": "demo", "scenario": "地面电站", "device_models": ["HAV3"]},
    )

    hits = await rag.search("户用逆变器怎么组网", 5, corpus_id="demo")
    assert hits
    assert hits[0].document.filename == "household.md"

    # 反向再问一次，确认不是「谁先入库谁排前」的偶然结果。
    hits = await rag.search("地面电站逆变器怎么组网", 5, corpus_id="demo")
    assert hits[0].document.filename == "ground.md"


def test_foreign_token_gate_separates_unknown_terms() -> None:
    """外文词是完整的、不会再被切分的 token，最适合当「语料认不认识」的判据。

    中文单字会被跨词切分污染（「安装环境」切出「装环」），所以中文那边只能靠
    缺失比例；外文词不存在这个问题。
    """
    from collections import Counter

    corpus = Counter({"modbus": 10, "afci": 14, "协": 16, "议": 37})

    # 语料里一个外文词都没有 → 判库外
    assert has_unknown_foreign_token(Counter({"python": 1, "装": 1}), corpus) is True
    # 外文词在语料里存在 → 话题在库内
    assert has_unknown_foreign_token(Counter({"modbus": 1, "协": 1}), corpus) is False
    # 只要有一个外文词认识，另一个不认识的就不该单独定罪
    assert has_unknown_foreign_token(Counter({"python": 1, "afci": 1}), corpus) is False
    # 纯中文查询不归这条判据管
    assert has_unknown_foreign_token(Counter({"合": 1, "母": 1}), corpus) is False


async def test_corpus_scope_gate_rejects_out_of_domain(db_session: AsyncSession) -> None:
    """语料够大时，问语料范围外的事直接返回空，不让模型拿到凑巧命中的段落。

    这是抗幻觉链路的入口：返回空 → Agent 侧同一份判据判定 → 回「请补充设备型号或现象」，
    而不是让模型凭记忆编一个答案。
    """
    rag = RAGService(db_session)
    # 正文刻意含「安装环境」，好让「Python 装环境」的中文部分能对上——
    # 这样它只能被外文词判据拦住，缺比例那条判据在这个例子里够不着阈值。
    body = "逆变器安装环境要求：避免阳光直射。RS485 走屏蔽双绞线，MPPT 跟踪组串电压。"
    for index in range(get_settings().retrieval_corpus_min_chunks + 5):
        await _add_chunk(db_session, f"pv-{index}.md", body, {"corpus_id": "demo"}, position=index)

    # 库内问题照常命中
    hits = await rag.search("MPPT 怎么跟踪组串电压", 3, corpus_id="demo")
    assert hits
    assert "MPPT" in hits[0].chunk.content

    # 外文词不在语料里 → 空结果
    assert await rag.search("Python 怎么装环境", 3, corpus_id="demo") == []
    # 中文词组整条都不在语料里 → 空结果
    assert await rag.search("房贷利率是多少", 3, corpus_id="demo") == []


async def test_corpus_scope_gate_stays_off_for_small_corpus(db_session: AsyncSession) -> None:
    """片段数不足门槛时判据不生效：库小只能说明语料词汇少，不能说明问题跑题。

    这里用同一句问法做前后对照，差别只在语料规模——先在只有一条片段的库里问，
    再补到门槛以上问。如果小库也拦，正常问题会被一起堵死。
    """
    rag = RAGService(db_session)
    body = "逆变器安装环境要求，避免阳光直射，RS485 通信。"
    await _add_chunk(db_session, "only.md", body, {"corpus_id": "demo"})

    # 片段数远低于门槛：即使外文词（Windows）不在语料里，也不走拒答。
    assert await rag.search("Windows 怎么安装系统", 3, corpus_id="demo")
    assert await rag.corpus_scope_reason("Windows 怎么安装系统", corpus_id="demo") is None

    # 补足片段跨过门槛后，同样的问法被拦下——差别只在语料规模。
    for index in range(get_settings().retrieval_corpus_min_chunks + 5):
        await _add_chunk(
            db_session, f"fill-{index}.md", body, {"corpus_id": "demo"}, position=index
        )
    assert await rag.search("Windows 怎么安装系统", 3, corpus_id="demo") == []


async def test_corpus_scope_reason_tells_which_gate_fired(db_session: AsyncSession) -> None:
    """语料外时必须说清是哪个判据命中——追问的措辞要按原因选。

    「措辞对不上」（换成本领域说法就能答）和「术语根本不在资料里」
    （该把话题拉回现场设备）是两种情形，只给一个 bool 的话上层只能编一句万能话。
    """
    rag = RAGService(db_session)
    body = "逆变器安装环境要求：避免阳光直射。RS485 走屏蔽双绞线，MPPT 跟踪组串电压。"
    for index in range(get_settings().retrieval_corpus_min_chunks + 5):
        await _add_chunk(db_session, f"pv-{index}.md", body, {"corpus_id": "demo"}, position=index)

    # 库内问题：没有原因可报
    assert await rag.corpus_scope_reason("MPPT 怎么跟踪组串电压", corpus_id="demo") is None
    # 外文词一个都不认识（中文部分「装环」「环境」能在语料里找到，够不着缺失比例那条）
    assert (
        await rag.corpus_scope_reason("Python 怎么装环境", corpus_id="demo")
        == "unknown_foreign_terms"
    )
    # 词组整条都不在语料里
    assert (
        await rag.corpus_scope_reason("房贷利率是多少", corpus_id="demo") == "missing_terminology"
    )


def _hit(version: str, content: str, score: float, filename: str) -> SearchHit:
    chunk = DocumentChunk(
        id=f"chunk-{version}-{score}",
        document_id=f"doc-{version}",
        position=0,
        content=content,
        chunk_metadata={
            "document_version": version,
            "scenario": "地面电站",
            "document_title": f"{version} 教材",
        },
        embedding=[],
    )
    document = SourceDocument(
        id=f"doc-{version}",
        filename=filename,
        content_type="text/markdown",
        checksum=f"sum-{version}",
    )
    return SearchHit(chunk, document, score)


def test_detect_conflicts_lists_both_versions() -> None:
    """分数接近、版本不同 → 并列列出，而不是替用户挑一个。"""
    hits = [
        _hit("V2.0", "V2.0 对 433 号点的定义：功率因数。", 0.80, "M7-教材.md"),
        _hit("第3册", "第3册对 433 号点的定义：无功功率。", 0.76, "第3册.md"),
    ]
    conflicts = RAGService.detect_conflicts(hits)
    assert [item.document_version for item in conflicts] == ["V2.0", "第3册"]
    assert conflicts[0].filenames == ["M7-教材.md"]
    assert conflicts[1].excerpts == ["第3册对 433 号点的定义：无功功率。"]


def test_detect_conflicts_skips_clear_loser_and_same_version() -> None:
    """分数拉开（另一个版本明显更弱）或同版本内多片段，都不算版本并存。"""
    clear = [
        _hit("V2.0", "强依据", 0.80, "a.md"),
        _hit("第3册", "弱依据", 0.40, "b.md"),  # 0.40 < 0.80 * 0.85
    ]
    assert RAGService.detect_conflicts(clear) == []

    same = [
        _hit("V2.0", "同一份资料的片段一", 0.80, "a.md"),
        _hit("V2.0", "同一份资料的片段二", 0.78, "a.md"),
    ]
    assert RAGService.detect_conflicts(same) == []


def test_detect_conflicts_ignores_chunks_without_version() -> None:
    """抽不到版本号的片段不参与判定：没有版本还报冲突只会制造噪音。"""
    hits = [
        _hit("V2.0", "有版本的依据", 0.80, "a.md"),
        SearchHit(
            DocumentChunk(
                id="c-none",
                document_id="d-none",
                position=0,
                content="上传的临时文档，没有版本标签",
                chunk_metadata={"filename": "upload.md"},
                embedding=[],
            ),
            SourceDocument(
                id="d-none", filename="upload.md", content_type="text/markdown", checksum="x"
            ),
            0.79,
        ),
    ]
    assert RAGService.detect_conflicts(hits) == []


def test_citations_carry_industry_labels() -> None:
    """引用要带上版本和机型，否则前端和用户都看不出这条依据适不适用。"""
    hit = _hit("V2.0", "绝缘阻抗低的处理步骤。", 0.7, "M6-维护指导.md")
    hit.chunk.chunk_metadata.update(
        build_chunk_metadata(
            document_id="M6",
            document_title="工商业解决方案维护指导（纯光场景）",
            section_title="6.2 绝缘阻抗故障位置定位",
            source_file="M6-工商业解决方案维护指导（纯光场景）.md",
            corpus_id="smartpv_v2",
            content="通过 RS485 读取组串绝缘阻抗，低于阈值告警。",
            document_version="V2.0",
        )
    )
    citation = RAGService.citations([hit])[0]
    assert citation.document_version == "V2.0"
    assert citation.scenario == "工商业"
    assert citation.protocols == ["RS485"]
