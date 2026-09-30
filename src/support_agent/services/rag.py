import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import DocumentChunk, SourceDocument
from ..schemas import Citation, VersionConflict
from .cache import RetrievalCache, get_retrieval_cache, load_hits_by_id
from .documents import PageText, checksum, chunk_pages, extract_pages
from .embeddings import (
    bigram_terms,
    cosine_similarity,
    idf_weight,
    retrieval_terms,
    strip_stopwords,
    tokenize,
    weighted_coverage,
)
from .industry import metadata_signals, query_facets
from .knowledge_base import KnowledgeChunk, load_smartpv_corpus
from .semantic import EmbeddingBackend, get_embedding_backend

logger = logging.getLogger("support_agent")

QUERY_FILLERS = ("请问", "是什么", "是啥", "如何处理", "怎么处理", "怎么办", "怎么解决")

# 三路文本信号的权重。它们管的是「这段话在不在讲我想问的事」。
TEXT_WEIGHTS = {"lexical": 0.30, "phrase": 0.35, "semantic": 0.35}

# 覆盖率锐化指数。覆盖率是线性的：命中 7 个实词里的 3 个就拿 0.43，
# 而这 3 个往往是「装」「用」「作」这类最普通的字。平方之后，
# 高覆盖的片段几乎不受影响、半覆盖的被压下去，库内与库外的分数才拉得开。
# 实测：不加锐化时间隔只有 0.016，平方后为 0.089。
COVERAGE_SHARPNESS = 2.0

# 哈希向量的可信度下限见 `semantic.HashEmbeddingBackend`：那是给字符级碰撞设的折扣，
# 真语义模型不打折。检索侧统一从 ``self.backend.semantic_trust_floor`` 取。

# 查询词组在整份语料里的缺失比例上限，超过就认为问题不在语料范围内。
#
# 光靠分数阈值拦不住库外问题。实测 30 条库内问题与 30 条库外问题：
# 库内最低 0.182（「Modbus 协议怎么读数据」），库外最高的三条是
# 「车险怎么买」0.353、「怎么学英语口语」0.340、「怎么申请护照」0.340——
# 它们赢在「险」「语」「照」这些单字恰好撞上了别的话题，分数比一些真问题还高。
# 试过 IDF 加权的四种变体、剔除语料外词、跨词词组过滤，全部仍然重叠：
# 哈希向量只是字符碰撞，「Python 装环境」和语料里的「安装环境」在字符层面
# 确实相似，靠统计手段分不开。根治要换成真正的语义向量（见 docs/architecture.md）。
#
# 所以改用第二个判据做分工：分数管「这段的字面覆盖够不够」，
# 缺失比例管「问题的词汇本身在不在语料里」。
CORPUS_MISSING_CEILING = 0.60

# 启用上面那条判据所需的最小语料规模（按**片段数**计，不按文档数）。
#
# 语料太小的时候，它本身就没覆盖多少词汇，「词组不存在」只能说明库小，
# 不能说明问题跑题——两个片段的库里问什么都是库外。所以规模不够时退回纯分数判据，
# 宁可多答，不要误拦。
#
# 实测语料 212 片段、库内问题缺失比例最高 0.50，库外从 0.60 起跳，
# 阈值取 0.60 时库内 30 条全部放行、库外成批拦截。
CORPUS_MISSING_MIN_CHUNKS = 60


def has_unknown_foreign_token(query_units: Counter[str], unit_df: Counter[str]) -> bool:
    """查询里的外文/数字词是否全都不在语料中。

    缺失比例那条判据漏掉了「Python 怎么装环境」这类问法：它的词组是
    「python / 装环 / 环境」，其中「装环」「环境」都能在语料里找到
    （「安装环境」被切成这两个组合），缺失比例只有 0.33，够不着 0.60。

    但中文单字会被跨词切分污染，判定不可靠；外文词不一样，它是完整、
    不可再分的 token——「Python」「Windows」在整份光伏资料里从未出现，
    基本可以断定问的不是这份资料里的事。

    用 all 而不是 any：查询里只要有一个外文词是语料认识的（AFCI、Modbus、
    RS485、SUN2000），就说明话题在库内，另一个不认识的外文词多半只是
    同一件东西的另一种叫法或某个具体型号，不该据此拒答。
    """
    foreign = [term for term in query_units if term.isascii() and len(term) > 1]
    return bool(foreign) and all(unit_df.get(term, 0) == 0 for term in foreign)


def out_of_corpus(
    query_units: Counter[str],
    query_phrases: Counter[str],
    unit_df: Counter[str],
    phrase_df: Counter[str],
    total_chunks: int,
) -> bool:
    """问题的词汇是否基本落在语料之外。

    抽成纯函数，是因为调用它的地方有两个：`search` 内部（检索前直接返回空）
    和 Agent 侧的前置拦截（在调模型之前就判定）。两处必须用同一套判定，
    否则会出现「检索说不该答、Agent 说可以问」这种自相矛盾。

    返回 True 只代表「这个问题用的词，语料基本没讲过」，
    不代表语料里一定没有答案——校准过的阈值只能做到这个程度。
    """
    return corpus_scope_reason(
        query_units, query_phrases, unit_df, phrase_df, total_chunks
    ) is not None


# 判为「语料范围外」的原因。两个分支的后续动作不同：
# 口语化措辞可以请用户换成本领域的说法再问一次，外文词不认识则多半真的不在资料范围内。
CorpusScopeReason = Literal["missing_terminology", "unknown_foreign_terms"]


def corpus_scope_reason(
    query_units: Counter[str],
    query_phrases: Counter[str],
    unit_df: Counter[str],
    phrase_df: Counter[str],
    total_chunks: int,
) -> CorpusScopeReason | None:
    """判为语料外时返回原因，在范围内则返回 None。

    判据本身与 `out_of_corpus` 完全一致，只是把「为什么」也一并带出来：
    Agent 侧要按原因决定追问的措辞，光知道是/否不够用。
    """
    if total_chunks < CORPUS_MISSING_MIN_CHUNKS:
        return None
    if query_phrases:
        missing = sum(1 for term in query_phrases if phrase_df.get(term, 0) == 0)
        if missing / len(query_phrases) >= CORPUS_MISSING_CEILING:
            return "missing_terminology"
    if has_unknown_foreign_token(query_units, unit_df):
        return "unknown_foreign_terms"
    return None


# 元数据加权：命中所问机型/协议/场景的片段往上抬，明确属于别的机型/场景的往下压。
# 用加减而不是乘除，是因为文本分集中在 0.3~0.8 这个区间，
# 乘法对小分值的压制太弱，加法更容易让「同类候选」排到「异类候选」前面。
MATCH_BONUS = 0.06
CONFLICT_PENALTY = 0.10

# 判定「版本并存」的分数带：差距在这个比例内的命中，视为同一档次的依据。
CONFLICT_SCORE_RATIO = 0.85
# 最多并列几个版本，避免一次检索把所有版本都铺出来。
CONFLICT_MAX_GROUPS = 3
# 每个版本最多摘几句，够用户判断依据来自哪里即可。
CONFLICT_EXCERPTS_PER_GROUP = 2
CONFLICT_EXCERPT_CHARS = 200


@dataclass(frozen=True)
class SearchHit:
    """一次检索命中，包含文本片段、来源文档和相关度分数。"""

    chunk: DocumentChunk
    document: SourceDocument
    score: float


@dataclass(frozen=True)
class CorpusIngestReport:
    """一次目录知识库导入的汇总结果。"""

    documents_created: int
    documents_skipped: int
    sections: int
    chunks_created: int


@dataclass(frozen=True)
class CorpusIndex:
    """一次检索范围内的片段与词频统计。

    检索和语料范围判定都要这份统计，抽出来是为了让两处用同一份，
    而不是各自算一遍——算两遍迟早会算出两个不同的结论。
    """

    entries: list[tuple[DocumentChunk, SourceDocument, Counter[str], Counter[str]]]
    unit_df: Counter[str]
    phrase_df: Counter[str]

    @property
    def total_chunks(self) -> int:
        return len(self.entries)


class RAGService:
    """负责文档入库、切块、向量生成、检索和引用格式转换。"""

    def __init__(
        self,
        db: AsyncSession,
        chunk_size: int = 700,
        overlap: int = 100,
        backend: EmbeddingBackend | None = None,
        cache: RetrievalCache | None = None,
    ):
        self.db = db
        self.chunk_size = chunk_size
        self.overlap = overlap
        # 入库和检索必须用同一个后端，否则存进去的向量和查询向量不在一个空间。
        # 默认按配置取，测试或脚本可以显式传进来。
        self.backend = backend or get_embedding_backend()
        # 缓存默认按配置取（没配 Redis 就是进程内实现）。传进来是为了让评测和
        # 测试能控制它——评测如果吃到上一轮留下的缓存，量到的就不是真实检索。
        self.cache = cache if cache is not None else get_retrieval_cache()

    @staticmethod
    def query_terms(query: str) -> tuple[str, Counter[str], Counter[str]]:
        """归一化查询：去掉客套问法、剥掉虚词，再切出单字与两字组合。

        前置拦截（在调模型之前判定话题是否在语料内）也要用同一套切词，
        所以单独抽出来，避免两处各切一遍、切法还不一样。
        """
        normalized = query
        for filler in QUERY_FILLERS:
            normalized = normalized.replace(filler, "")
        normalized = normalized.strip() or query
        key = strip_stopwords(normalized) or normalized
        return key, Counter(tokenize(key)), Counter(retrieval_terms(key))

    async def corpus_index(
        self, *, corpus_id: str | None = None, include_restricted: bool = False,
        visibility: str | None = None,
    ) -> CorpusIndex:
        """读取权限范围内的片段，切词并统计词频（df）。"""
        rows = (
            await self.db.execute(
                select(DocumentChunk, SourceDocument).join(
                    SourceDocument, SourceDocument.id == DocumentChunk.document_id
                )
            )
        ).all()
        entries: list[tuple[DocumentChunk, SourceDocument, Counter[str], Counter[str]]] = []
        unit_df: Counter[str] = Counter()
        phrase_df: Counter[str] = Counter()
        for chunk, document in rows:
            metadata = chunk.chunk_metadata or {}
            # 未标记的历史资料一律视为内部资料，不能因升级而变成可外发内容。
            is_public = metadata.get("visibility") == "public"
            if (visibility == "public" and not is_public) or (
                visibility == "private" and is_public
            ):
                continue
            if corpus_id and metadata.get("corpus_id") != corpus_id:
                continue
            if not include_restricted and metadata.get("restricted") is True:
                continue
            chunk_units = Counter(tokenize(chunk.content))
            chunk_phrases = Counter(bigram_terms(chunk.content))
            entries.append((chunk, document, chunk_units, chunk_phrases))
            # 用 set 计文档频率：一个词在一段里出现几次，只说明这段啰嗦，
            # 不代表它更能区分话题。
            unit_df.update(chunk_units.keys())
            phrase_df.update(chunk_phrases.keys())
        return CorpusIndex(entries, unit_df, phrase_df)

    async def corpus_scope_reason(
        self, query: str, *, corpus_id: str | None = None, include_restricted: bool = False
    ) -> CorpusScopeReason | None:
        """这段问题在语料范围外的话，返回原因；在范围内返回 None。

        供上层在调用模型之前判定。实测模型遇到明显跑题的问题时，根本不会去调检索工具——
        它直接凭「我是光伏助手」拒答，服务端那句「检索过但没有依据」的兜底永远等不到，
        判定实际落在模型手里。所以在调模型之前先自己判一次。

        这里调的是模块级的同名纯函数，和 `search` 内部那条判据共用一份实现与一份语料统计。
        """
        _, query_units, query_phrases = self.query_terms(query)
        index = await self.corpus_index(corpus_id=corpus_id, include_restricted=include_restricted)
        return corpus_scope_reason(
            query_units, query_phrases, index.unit_df, index.phrase_df, index.total_chunks
        )

    async def is_out_of_corpus(
        self, query: str, *, corpus_id: str | None = None, include_restricted: bool = False
    ) -> bool:
        """`corpus_scope_reason` 的布尔形式，保留给只关心是/否、不需要原因的调用方。"""
        reason = await self.corpus_scope_reason(
            query, corpus_id=corpus_id, include_restricted=include_restricted
        )
        return reason is not None

    async def ingest(
        self, filename: str, content_type: str, data: bytes, *, visibility: str = "private"
    ) -> tuple[SourceDocument, int, bool]:
        """导入文档；相同内容通过校验和去重，不重复生成文本片段。"""
        digest = checksum(data)
        if visibility not in {"private", "public"}:
            raise ValueError("资料级别必须是 private 或 public")
        existing = await self.db.scalar(
            select(SourceDocument).where(SourceDocument.checksum == digest)
        )
        if existing:
            count_result = await self.db.scalars(
                select(DocumentChunk).where(DocumentChunk.document_id == existing.id)
            )
            return existing, len(count_result.all()), True

        pages = extract_pages(filename, data)
        pieces = chunk_pages(pages, self.chunk_size, self.overlap)
        if not pieces:
            raise ValueError("文件中没有可索引的文本")
        document = SourceDocument(filename=filename, content_type=content_type, checksum=digest)
        self.db.add(document)
        await self.db.flush()
        # 一次把整份文档交给后端，后端内部再分批——逐片调用会让 ONNX 每片都要
        # 重新拼一次张量，212 片的语料导入时间差好几倍。
        vectors = await self.backend.embed_async([piece.text for piece in pieces])
        for position, (piece, vector) in enumerate(zip(pieces, vectors, strict=True)):
            self.db.add(
                DocumentChunk(
                    document_id=document.id,
                    position=position,
                    page=piece.page,
                    content=piece.text,
                    chunk_metadata={
                        "filename": filename,
                        "visibility": visibility,
                        "embedding_signature": self.backend.signature,
                    },
                    embedding=vector,
                )
            )
        await self.db.commit()
        # 语料变了就换代次，既有检索缓存立刻作废。不做这一步的话，
        # 新导入的资料要等缓存 TTL 到期才可能被检索到——表现为「明明导进去了却查不到」。
        await self.cache.invalidate()
        return document, len(pieces), False

    async def ingest_smartpv_corpus(
        self, root: Path, *, include_restricted: bool = False
    ) -> CorpusIngestReport:
        """把本地 SmartPV 分卷按章节导入数据库，并保留检索元数据。"""
        sections = load_smartpv_corpus(root, include_restricted=include_restricted)
        by_source: dict[Path, list[KnowledgeChunk]] = defaultdict(list)
        for section in sections:
            by_source[section.source_path].append(section)

        documents_created = 0
        documents_skipped = 0
        chunks_created = 0
        for source_path, source_sections in by_source.items():
            data = source_path.read_bytes()
            digest = checksum(data)
            existing = await self.db.scalar(
                select(SourceDocument).where(SourceDocument.checksum == digest)
            )
            if existing:
                documents_skipped += 1
                continue

            document = SourceDocument(
                filename=source_path.name,
                content_type="text/markdown",
                checksum=digest,
            )
            self.db.add(document)
            await self.db.flush()
            documents_created += 1

            # 先把整份分卷的片段收齐再一次性算向量，避免逐片推理。
            pending: list[tuple[dict, PageText]] = []
            for section in source_sections:
                pieces = chunk_pages(
                    [PageText(page=section.page_start, text=section.content)],
                    self.chunk_size,
                    self.overlap,
                )
                pending.extend((section.metadata, piece) for piece in pieces)
            vectors = await self.backend.embed_async([piece.text for _, piece in pending])

            for position, ((metadata, piece), vector) in enumerate(
                zip(pending, vectors, strict=True)
            ):
                self.db.add(
                    DocumentChunk(
                        document_id=document.id,
                        position=position,
                        page=piece.page,
                        content=piece.text,
                        chunk_metadata={
                            **metadata,
                            "visibility": "private",
                            "embedding_signature": self.backend.signature,
                        },
                        embedding=vector,
                    )
                )
                chunks_created += 1

        await self.db.commit()
        if documents_created:
            # 全部按校验和跳过时语料没有变化，不必换代次——代次一动，
            # 所有既有缓存都作废，命中率白掉一次。
            await self.cache.invalidate()
        return CorpusIngestReport(
            documents_created=documents_created,
            documents_skipped=documents_skipped,
            sections=len(sections),
            chunks_created=chunks_created,
        )

    async def search(
        self,
        query: str,
        top_k: int = 5,
        *,
        corpus_id: str | None = None,
        include_restricted: bool = False,
        min_score: float = 0.0,
        visibility: str | None = None,
    ) -> list[SearchHit]:
        """在指定语料库和权限范围内进行混合检索。

        打分分两段：先算文本相似度（单字 / 两字组合 / 哈希向量），
        再用片段的行业标签做加减——用户问「户用 SUN2000」时，
        一块「地面电站」的章节哪怕用词更像，也不该排到前面去。

        词面重叠按 IDF 加权（见 `embeddings.idf_weight`），否则「装」「环」「境」
        这类高频字会和「合母」一样算作命中，库外问题也能拿到不低的分。

        这里刻意**不做硬过滤**。行业语料对机型和版本的覆盖并不完整，
        「先按型号过滤」会把跨机型通用的章节一起丢掉，模型反而拿不到任何依据。
        所以元数据只影响排序，最终取舍仍由分数决定。
        """
        # 先把虚词和口语词剥掉，再算三路信号。不剥的话分母会把「啥」「是」
        # 这些没信息量的字也算进去，真正的问题词反被摊薄。
        key_query, query_units, query_phrases = self.query_terms(query)
        # 缓存键必须带上所有会改变结果的输入：语料、权限、条数、分数阈值、向量后端指纹。
        # 少带任何一个，都会变成「改了配置却还读到旧结果」这类最难排查的问题。
        # 用归一化后的 key_query 而不是原始问句：剥停用词不改变语义，
        # 「啥是控母」和「控母」本来就该共用一份缓存。语料变更由代次负责失效。
        cache_parts = (
            key_query,
            top_k,
            corpus_id or "",
            include_restricted,
            visibility or "all",
            min_score,
            self.backend.signature,
        )
        cached = await self.cache.get(cache_parts)
        if cached is not None:
            return await self._hits_from_cache(cached, corpus_id, include_restricted, visibility)

        query_embedding = (await self.backend.embed_async([key_query]))[0]
        facets = query_facets(key_query)
        index = await self.corpus_index(
            corpus_id=corpus_id, include_restricted=include_restricted, visibility=visibility
        )

        # 库外判据（见 CORPUS_MISSING_CEILING 与 has_unknown_foreign_token）：
        # 问题用的词基本不在语料里时直接返回空结果，上游会把它当成
        # 「检索过但没有依据」，走拒答而不是让模型凭记忆作答。
        if out_of_corpus(
            query_units, query_phrases, index.unit_df, index.phrase_df, index.total_chunks
        ):
            # 空结果也值得缓存：跑题问题会被反复问到，而每次判定都要读一遍
            # 全语料并切词统计词频，这一步比算分本身还贵。
            await self.cache.put(cache_parts, [])
            return []

        total_chunks = index.total_chunks
        unit_weights = {
            term: idf_weight(index.unit_df.get(term, 0), total_chunks) for term in query_units
        }
        phrase_weights = {
            term: idf_weight(index.phrase_df.get(term, 0), total_chunks) for term in query_phrases
        }

        # 第二遍：算分。
        # 三路信号各管一段：
        #   单字重叠 —— 管召回，口语和书面语用字不同也能沾上边；
        #   两字组合 —— 管区分，「合母」和「控母」共享「母」，只有组合后才能分开；
        #   向量相似度 —— 语义模型下管「换种说法也能答上」，哈希向量下只兜字符级相似。
        hits: list[SearchHit] = []
        mismatched = 0
        for chunk, document, chunk_units, chunk_phrases in index.entries:
            lexical = weighted_coverage(query_units, chunk_units, unit_weights)
            phrase = weighted_coverage(query_phrases, chunk_phrases, phrase_weights)
            stored = list(chunk.embedding or [])
            # 库里的向量必须和当前查询向量同源同长。换过 embedding 后端或维度时，
            # 旧向量和新查询向量不在一个空间，算出来的相似度没有意义——余弦相似度
            # 在长度不等时会按短的截断，静默给出一个看着正常的假分数。所以宁可
            # 这一路信号记 0，也不要拿假分数参与排序。
            if len(stored) != len(query_embedding):
                mismatched += 1
                semantic = 0.0
            else:
                semantic = max(cosine_similarity(query_embedding, stored), 0.0)
            # 哈希向量在覆盖率低时多半是常用字碰撞出来的，此时把语义分压下去；
            # 真语义模型没有这个毛病，折扣取 1（见 semantic.EmbeddingBackend）。
            floor = self.backend.semantic_trust_floor
            semantic *= floor + (1 - floor) * lexical
            score = (
                TEXT_WEIGHTS["lexical"] * lexical**COVERAGE_SHARPNESS
                + TEXT_WEIGHTS["phrase"] * phrase**COVERAGE_SHARPNESS
                + TEXT_WEIGHTS["semantic"] * semantic
            )
            signals = metadata_signals(facets, chunk.chunk_metadata or {})
            if signals.matched:
                score += MATCH_BONUS * len(signals.matched)
            if signals.conflicted:
                score -= CONFLICT_PENALTY * len(signals.conflicted)
            if score > 0 and score >= min_score:
                hits.append(SearchHit(chunk, document, score))
        ranked = sorted(hits, key=lambda hit: hit.score, reverse=True)[:top_k]
        if mismatched:
            # 不抛异常：语料里混着旧向量时，检索还能靠词面两路给出结果，
            # 但必须留下痕迹，否则「语义分一直是 0」会被当成模型效果差。
            logger.warning(
                "检测到 %d 个片段的向量长度与当前后端不一致（当前 %d 维，后端 %s），"
                "这些片段的向量信号已按 0 处理；换 embedding 后端后需重新导入语料",
                mismatched,
                len(query_embedding),
                self.backend.signature,
            )
            # 这一次的排序结果是带病的（部分片段的语义信号被丢弃），不写缓存：
            # 写进去之后警告只会出现第一次，后面全吃缓存，问题反而被盖住。
            return ranked
        await self.cache.put(cache_parts, [(hit.chunk.id, hit.score) for hit in ranked])
        return ranked

    async def _hits_from_cache(
        self,
        entries: list[tuple[int, float]],
        corpus_id: str | None,
        include_restricted: bool,
        visibility: str | None = None,
    ) -> list[SearchHit]:
        """把缓存的 (片段 id, 分数) 还原成检索结果。

        分数直接用缓存里的，片段正文回库取——这样既不用把 ORM 对象塞进缓存，
        也能让片段被删掉或被移出语料时自然少返回，不会拿旧快照当依据。
        """
        rows = await load_hits_by_id(
            self.db,
            entries,
            corpus_id=corpus_id,
            include_restricted=include_restricted,
            visibility=visibility,
        )
        return [SearchHit(chunk, document, score) for chunk, document, score in rows]

    @staticmethod
    def detect_conflicts(hits: list[SearchHit]) -> list[VersionConflict]:
        """找出「同一档次、但出自不同资料版本」的依据，并列返回而不是替用户选一个。

        判定条件是分数接近 + 版本不同，**不等于**两份资料互相矛盾：教材章节和
        分册资料讲同一件事，措辞和详略本来就不同。这里的语义是提示——
        「这个问题的依据来自两个版本，请按现场型号和厂家正式资料确认」，
        而不是断言谁对谁错。真要在资料层面判矛盾，需要逐条对齐结构化点表，
        那是下一步的事。

        版本号抽不到的片段不参与判定：拿不到版本还去报冲突，只会制造噪音。
        """
        if not hits:
            return []
        best = hits[0].score
        if best <= 0:
            return []
        threshold = best * CONFLICT_SCORE_RATIO
        grouped: dict[str, list[SearchHit]] = defaultdict(list)
        for hit in hits:
            if hit.score < threshold:
                continue
            version = (hit.chunk.chunk_metadata or {}).get("document_version")
            if not version:
                continue
            grouped[str(version)].append(hit)
        if len(grouped) < 2:
            return []
        conflicts: list[VersionConflict] = []
        # 每组用该版本内分数最高的一条作代表，版本之间按代表分数排序。
        ordered = sorted(
            grouped.items(),
            key=lambda item: max(hit.score for hit in item[1]),
            reverse=True,
        )[:CONFLICT_MAX_GROUPS]
        for version, group in ordered:
            representatives = sorted(group, key=lambda hit: hit.score, reverse=True)[
                :CONFLICT_EXCERPTS_PER_GROUP
            ]
            conflicts.append(
                VersionConflict(
                    document_version=version,
                    document_ids=sorted({hit.document.id for hit in group}),
                    filenames=sorted({hit.document.filename for hit in group}),
                    excerpts=[
                        hit.chunk.content[:CONFLICT_EXCERPT_CHARS] for hit in representatives
                    ],
                    score=round(max(hit.score for hit in group), 4),
                )
            )
        return conflicts

    @staticmethod
    def citations(hits: list[SearchHit]) -> list[Citation]:
        return [
            Citation(
                document_id=hit.document.id,
                filename=hit.document.filename,
                chunk_id=hit.chunk.id,
                page=hit.chunk.page,
                excerpt=hit.chunk.content[:240],
                score=round(hit.score, 4),
                document_version=(hit.chunk.chunk_metadata or {}).get("document_version"),
                scenario=(hit.chunk.chunk_metadata or {}).get("scenario"),
                protocols=list((hit.chunk.chunk_metadata or {}).get("protocols") or []),
                device_models=list((hit.chunk.chunk_metadata or {}).get("device_models") or []),
            )
            for hit in hits
        ]
