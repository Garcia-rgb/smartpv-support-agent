"""本机 OCR 与有依据的刷题判断。题图不会写入知识库或发送到外部服务。"""

import re
import sys
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import DocumentChunk, SourceDocument
from ..schemas import Citation, QuizResponse
from .rag import RAGService

OPTION_RE = re.compile(r"^([A-F])(?:\s*[.、．:：)）]\s*|\s+|(?=[\u4e00-\u9fff]))(.+)$", re.I)
# OCR 有时把选项字母单独切成一个文本框（"C" 一行、"25kW" 一行）。
SINGLE_LETTER_RE = re.compile(r"^([A-F])\s*[.、．:：)）]?$", re.I)
IMAGE_SIGNATURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF")


@lru_cache(maxsize=1)
def _ocr_engine():
    # 便于源码运行时从仓库内的本地依赖目录加载；正式安装可用 [quiz] extra。
    local = Path(__file__).resolve().parents[3] / ".localdeps"
    if local.is_dir() and str(local) not in sys.path:
        sys.path.append(str(local))
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise RuntimeError("本地图片识别组件未安装，请安装项目的 quiz 依赖") from exc
    return RapidOCR()


def recognize_image(data: bytes) -> str:
    if not data.startswith(IMAGE_SIGNATURES):
        raise ValueError("请上传 PNG、JPG 或 WebP 图片")
    try:
        result = _ocr_engine()(data)
    except Exception as exc:
        raise ValueError("图片无法识别，请换一张清晰截图") from exc
    return "\n".join(result.txts or ())


def parse_question(raw_text: str) -> tuple[str, str, dict[str, str]]:
    lines = [re.sub(r"\s+", " ", x).strip() for x in raw_text.splitlines() if x.strip()]
    kind = "判断" if lines and lines[0] in {"判断", "判断题", "正确或错误"} else "单选"
    labels = {"判断", "判断题", "正确或错误", "选择", "选择题", "单选", "单选题", "多选", "多选题"}
    if lines and lines[0] in labels:
        kind = "多选" if lines[0] in {"多选", "多选题"} else kind
        lines = lines[1:]
    stem: list[str] = []
    options: dict[str, str] = {}
    pending: str | None = None
    for line in lines:
        single = SINGLE_LETTER_RE.match(line)
        if single and stem:
            pending = single.group(1).upper()
            continue
        match = OPTION_RE.match(line)
        if match and stem:
            options[match.group(1).upper()] = match.group(2).strip()
            pending = None
        elif pending:
            # 挂起的字母等到了它的选项内容，合并成一个选项。
            options[pending] = line
            pending = None
        elif options and kind == "多选":
            # 多选题图的 OCR 经常直接丢掉后面的选项字母（如只剩 A、B 两行带字母），
            # 此时按字母顺序顺延出一个新选项；单选题不冒这个险，仍并入上一选项。
            next_letter = chr(ord(max(options)) + 1)
            if next_letter <= "F":
                options[next_letter] = line
            else:
                options[max(options)] += line
        elif options:
            last = list(options)[-1]
            options[last] += line
        else:
            stem.append(line)
    question = "".join(stem).strip()
    if not question or len(options) < 2:
        raise ValueError("没有识别出完整题干和至少两个选项，请修改识别文字后重试")
    if {v.replace(" ", "") for v in options.values()} == {"正确", "错误"}:
        kind = "判断"
    return kind, question, options


def _normal(text: str) -> str:
    return re.sub(r"[\s，,。·（）()：:；;、*/*]", "", text).lower()


def _altitude_rule(question: str, options: dict[str, str]) -> str | None:
    """只有题意明确覆盖 M11 的高空作业阈值和安全带时才判断。"""
    compact = _normal(question)
    threshold = re.search(r"(?:2(?:\.0)?米|2m)", compact)
    protection = "安全带" in compact and any(word in compact for word in ("佩戴", "穿戴", "系"))
    at_or_above = any(word in compact for word in ("达到或超过", "大于等于", "及以上", "以上", "≥"))
    negated = any(word in compact for word in ("不必", "无需", "不用", "禁止佩戴"))
    if not (threshold and protection and at_or_above) or negated:
        return None
    for key, value in options.items():
        if value in {"正确", "对", "是", "√"}:
            return key
    return None


def _bank_match(
    question: str, options: dict[str, str], rows, *, multi: bool = False
) -> tuple[list[str], object, object] | None:
    """只匹配已校对的题目/答案对。

    单选题要求恰好一个选项文字出现在参考答案里；多选题允许多个，
    全部匹配上的选项一起作为答案返回。
    """
    normalized_question = _normal(question)
    ranked = []
    for chunk, doc in rows:
        source_question = re.search(r"题目：(.+)", chunk.content)
        source_answer = re.search(r"参考答案：(.+)", chunk.content)
        if not source_question or not source_answer:
            continue
        similarity = SequenceMatcher(
            None, normalized_question, _normal(source_question.group(1))
        ).ratio()
        ranked.append((similarity, source_answer.group(1), chunk, doc))
    if not ranked:
        return None
    similarity, answer, chunk, doc = max(ranked, key=lambda row: row[0])
    if similarity < 0.62:
        return None
    matches = [
        key for key, value in options.items()
        if len(_normal(value)) >= 2 and _normal(value) in _normal(answer)
    ]
    if not matches or (len(matches) > 1 and not multi):
        return None
    return matches, chunk, doc


def _evidence_match(
    question: str, options: dict[str, str], hits, *, multi: bool = False
) -> tuple[list[str], object, object] | None:
    """检索证据推断：题库没有的题，用 top-k 检索原文判断选项。

    选项文字（去标点后 ≥2 字）原样出现在原文里才算被支持：
    单选要求恰好一个选项被支持（多了算歧义，宁可不答也不错答）；
    多选要求至少两个。判断题不走这条路。
    """
    evidence = _normal("".join(h.chunk.content for h in hits))
    if not evidence:
        return None
    supported = [
        key for key, value in options.items()
        if len(_normal(value)) >= 2 and _normal(value) in evidence
    ]
    if multi:
        ok = len(supported) >= 2
    else:
        ok = len(supported) == 1
    if not ok:
        return None
    return supported, hits[0].chunk, hits[0].document


async def _semantic_disambiguate(
    question: str,
    options: dict[str, str],
    rag: RAGService,
    *,
    top_k: int = 2,
    lead: float = 0.03,
    visibility: str | None = None,
) -> tuple[str, dict[str, float], list] | None:
    """子串证据判不出（多个选项都命中原文或都命中不了）时的兜底。

    把「题干 + 选项」当查询逐项检索，哪个选项的检索分明显领先就选哪个。
    领先阈值 lead 用来挡「四个选项分数差不多」的瞎猜——分不开就宁可答不出。
    只对单选题启用；多选的组合空间太大，逐项打分不可靠。
    """
    scores: dict[str, float] = {}
    per_key_hits: dict[str, list] = {}
    for key, value in options.items():
        if len(_normal(value)) < 2:
            continue
        hits = await rag.search(f"{question} {value}", top_k=top_k, visibility=visibility)
        if hits:
            scores[key] = hits[0].score
            per_key_hits[key] = hits
    if len(scores) < 2:
        return None
    ranked = sorted(scores, key=scores.get, reverse=True)
    best, second = ranked[0], ranked[1]
    if scores[best] - scores[second] < lead:
        return None
    return best, scores, per_key_hits[best]


_QUERY_NOISE = (
    "正确的操作顺序是",
    "操作顺序是",
    "正确的是",
    "错误的是",
    "说法正确的是",
    "描述正确的是",
    "下列哪",
    "主要的步骤有",
    "的步骤有",
    "主要包括",
    "分别是",
    "是多少",
    "有哪些",
    "哪个",
    "哪些",
    "吗",
    "？",
    "?",
    "，",
    "、",
    "。",
)


def _query_variants(question: str) -> list[str]:
    """生成检索查询变体：完整题干 + 去掉疑问套话的精简版。

    完整题干带「正确的操作顺序是」这类套话会把混合检索的关键词权重稀释掉
    （实测 0.40 vs 0.62），所以精简版往往命中更好；两路合并取并集。
    """
    compact = re.sub(r"\s+", "", question)
    refined = compact
    for phrase in _QUERY_NOISE:
        refined = refined.replace(phrase, " ")
    refined = re.sub(r"\s+", " ", refined).strip()
    variants = [question]
    if len(refined) >= 4 and refined != compact:
        variants.append(refined)
    return variants


async def _search_question(
    rag: RAGService, question: str, top_k: int = 6, *, visibility: str | None = None
):
    seen: set[int] = set()
    merged = []
    for query in _query_variants(question):
        for hit in await rag.search(query, top_k=top_k, visibility=visibility):
            key = id(hit.chunk)
            if key not in seen:
                seen.add(key)
                merged.append(hit)
    merged.sort(key=lambda h: h.score, reverse=True)
    return merged[:top_k]


async def answer_question(
    db: AsyncSession,
    raw_text: str,
    *,
    chunk_size: int = 700,
    overlap: int = 100,
    llm_client: object | None = None,
    visibility: str | None = None,
) -> QuizResponse:
    kind, question, options = parse_question(raw_text)
    rag = RAGService(db, chunk_size, overlap)
    hits = await _search_question(rag, question, visibility=visibility)
    hcsa_hits = [h for h in hits if "HCSA" in h.document.filename or "M11" in h.document.filename]
    citations = RAGService.citations(hcsa_hits[:2])
    selected: list[str] = []
    explanation = "当前资料没有足够明确的答案，建议核对教材对应章节后再作答。"
    status = "insufficient_evidence"

    altitude = _altitude_rule(question, options)
    if altitude:
        # 规则也必须有本地原文支持，不能只凭代码写死的常识给出选项。
        row = (
            await db.execute(
                select(DocumentChunk, SourceDocument)
                .join(SourceDocument, SourceDocument.id == DocumentChunk.document_id)
                .where(SourceDocument.filename.contains("M11-"))
            )
        ).all()
        row = [r for r in row if (r[0].chunk_metadata or {}).get("enabled") is not False]
        if visibility == "public":
            row = [r for r in row if (r[0].chunk_metadata or {}).get("visibility") == "public"]
        evidence = next(
            (
                (chunk, doc)
                for chunk, doc in row
                if "高空作业定义" in chunk.content
                and "2 m" in chunk.content
            ),
            None,
        )
        ppe_evidence = any("安全带" in chunk.content for chunk, _ in row)
        if evidence and ppe_evidence:
            chunk, doc = evidence
            selected = [altitude]
            explanation = (
                "资料把有坠落风险且坠落高度达到 2 米的作业列为高空作业，"
                "并将安全衣、安全带列为防护装备。因此选正确。"
                "实际登高还要求有人看护、系索固定在两个不同点。"
            )
            status = "answered"
            citations = [
                Citation(
                    document_id=doc.id,
                    filename=doc.filename,
                    chunk_id=chunk.id,
                    page=chunk.page,
                    excerpt=chunk.content[max(0, chunk.content.find("高空作业定义") - 12):][:300],
                    score=1.0,
                    document_version=(chunk.chunk_metadata or {}).get("document_version"),
                )
            ]

    if not selected and kind != "判断":
        bank_rows = (
            await db.execute(
                select(DocumentChunk, SourceDocument)
                .join(SourceDocument, SourceDocument.id == DocumentChunk.document_id)
                # 同时覆盖「模拟题逐题解析」与后续追加的补充题解析文档。
                .where(SourceDocument.filename.contains("题解析"))
            )
        ).all()
        bank_rows = [r for r in bank_rows
                     if (r[0].chunk_metadata or {}).get("enabled") is not False]
        if visibility == "public":
            bank_rows = [
                r for r in bank_rows if (r[0].chunk_metadata or {}).get("visibility") == "public"
            ]
        bank = _bank_match(question, options, bank_rows, multi=kind == "多选")
        if bank:
            keys, chunk, doc = bank
            selected = keys
            status = "answered"
            answer_line = re.search(r"参考答案：(.+)", chunk.content).group(1)
            explanation = (
                f"题库参考答案：{answer_line[:160].rstrip('。；;')}。"
                f"与 {'、'.join(keys)} 项一致。"
            )
            citations = [
                Citation(
                    document_id=doc.id,
                    filename=doc.filename,
                    chunk_id=chunk.id,
                    page=chunk.page,
                    excerpt=chunk.content[:240],
                    score=1.0,
                    document_version=(chunk.chunk_metadata or {}).get("document_version"),
                )
            ]

    if not selected:
        evidence = _evidence_match(question, options, hits, multi=kind == "多选")
        if evidence:
            keys, chunk, doc = evidence
            selected = keys
            status = "answered"
            # 从原文里截出包含选项的那一段，让用户能直接核对。
            first_raw = options[keys[0]]
            excerpt_chunk = next(
                (h.chunk for h in hits if first_raw in h.chunk.content), chunk
            )
            pos = excerpt_chunk.content.find(first_raw)
            start = max(0, pos - 80)
            excerpt = excerpt_chunk.content[start : pos + 160].strip()
            explanation = (
                f"在资料《{doc.filename}》原文中找到依据：{excerpt}"
                + ("……" if pos + 160 < len(excerpt_chunk.content) else "")
                + f" 选项 {'、'.join(keys)} 与原文表述一致"
                + ("，其余选项未在原文出现。" if len(keys) < len(options) else "。")
            )
            citations = RAGService.citations((hcsa_hits or hits)[:2])

    if not selected and kind == "单选" and 2 <= len(options) <= 6:
        disambiguated = await _semantic_disambiguate(
            question, options, rag, visibility=visibility
        )
        if disambiguated:
            key, scores, best_hits = disambiguated
            selected = [key]
            status = "answered"
            top = best_hits[0]
            explanation = (
                "按「题干+选项」逐项检索本地资料，各选项最高相关度："
                + "、".join(f"{k}={scores[k]:.2f}" for k in sorted(scores))
                + f"。{key} 明显领先，依据《{top.document.filename}》"
                + (f"第{top.chunk.page}页：" if top.chunk.page else "：")
                + top.chunk.content[:200].replace("\n", " ")
            )
            citations = RAGService.citations((hcsa_hits or best_hits)[:2])

    if not selected and llm_client is not None:
        # 本地规则一路判不出，才把检索到的原文连同题目交给远程模型读。
        # 顺序上让确定性规则优先：省调用、也保证有确切依据时答案不被模型改写。
        if getattr(llm_client, "enabled", False):
            try:
                keys, reason = await llm_client.select_options(
                    question,
                    options,
                    [h.chunk.content for h in hits[:4]],
                    multi=kind == "多选",
                )
            except Exception:
                keys, reason = [], ""
            if keys:
                selected = keys
                status = "answered"
                explanation = (
                    f"{'本地' if getattr(llm_client, 'local', False) else '远程'}"
                    f"模型依据资料原文作答：{reason}"
                    if reason
                    else f"模型依据资料原文选择 {'、'.join(keys)}。"
                )
                citations = RAGService.citations((hcsa_hits or hits)[:2])

    return QuizResponse(
        status=status,
        question_type=kind,
        question=question,
        options=options,
        selected_options=selected,
        explanation=explanation,
        citations=citations,
        recognized_text=raw_text,
    )
