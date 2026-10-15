"""把单份本地文档（docx / md / txt）导入当前 DATABASE_URL 指向的知识库。

与 `scripts/ingest_smartpv.py` 的分工：

- 那个脚本读的是 HCSA 分卷的固定目录结构（`index.json` + `知识库分卷/`），
  只认那一套文件命名，不能用来追加零散资料。
- 本脚本处理「零散追加」的来源——例如微信收到的分册 Word 稿——并把元数据
  补成与 HCSA 语料同构（同一个 `corpus_id`），因此两批资料在检索时属于同一个库，
  不会因为 `RETRIEVAL_CORPUS_ID` 过滤而互相看不见。

docx 正文直接解析包内 `word/document.xml`，不依赖 python-docx。
标题样式 Heading1/2 映射为 Markdown 的 `##`/`###`，从而复用 HCSA 语料的
章节切分规则（`split_markdown_sections`）。

用法（在仓库根目录执行）：

    # 先干跑，确认章节切分是否正确
    python scripts/ingest_document.py --file "C:/path/第3册.docx" --dry-run

    # 正式导入，同时把转换出的 Markdown 落盘存档
    python scripts/ingest_document.py --file "C:/path/第3册.docx" \
        --out "D:/资料汇总/拓展资料/第3册-交直流屏柜原理.md" \
        --document-id V3 --title "第3册 交直流屏柜原理（结合图纸扩充版）"

同一份内容重复执行会按校验和跳过，不会产生重复片段。
"""

from __future__ import annotations

import argparse
import asyncio
import zipfile
from collections.abc import Sequence
from pathlib import Path
from xml.etree import ElementTree as ET

from sqlalchemy import select

from support_agent.config import get_settings
from support_agent.db import SessionFactory, create_schema
from support_agent.models import DocumentChunk, SourceDocument
from support_agent.services.cache import get_retrieval_cache
from support_agent.services.documents import PageText, checksum, chunk_pages
from support_agent.services.knowledge_base import (
    RESTRICTED_SECTION_TERMS,
    SMARTPV_CORPUS_ID,
    KnowledgeChunk,
    split_markdown_sections,
)
from support_agent.services.semantic import get_embedding_backend

WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
HEADING_STYLES = {f"Heading{level}": level for level in range(1, 10)}
# docx 的 Heading1 对应 Markdown 的 ##：HCSA 语料以二级标题作为章节边界。
HEADING_OFFSET = 1


def _paragraph_text(node: ET.Element) -> str:
    return "".join(part.text or "" for part in node.iter(f"{WORD_NS}t")).strip()


def _paragraph_style(node: ET.Element) -> str:
    properties = node.find(f"{WORD_NS}pPr")
    if properties is None:
        return ""
    style = properties.find(f"{WORD_NS}pStyle")
    if style is None:
        return ""
    return style.get(f"{WORD_NS}val") or ""


def _list_marker(node: ET.Element, style: str) -> str:
    """识别项目符号 / 编号列表，返回 Markdown 行首标记；普通段落返回空串。"""
    if style.startswith("ListBullet"):
        return "- "
    if style.startswith("ListNumber"):
        return "1. "
    properties = node.find(f"{WORD_NS}pPr")
    if properties is not None and properties.find(f"{WORD_NS}numPr") is not None:
        return "- "
    return ""


def _cell_text(cell: ET.Element) -> str:
    parts = [_paragraph_text(node) for node in cell.findall(f"{WORD_NS}p")]
    return " ".join(part for part in parts if part)


def _table_to_markdown(node: ET.Element) -> list[str]:
    """把 docx 表格转成 Markdown 表格，首行作表头。"""
    rows: list[list[str]] = []
    for row in node.findall(f"{WORD_NS}tr"):
        cells = [_cell_text(cell).replace("|", "\\|") for cell in row.findall(f"{WORD_NS}tc")]
        rows.append(cells)
    if not rows:
        return []
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(padded[0]) + " |", "|" + "---|" * width]
    lines.extend("| " + " | ".join(row) + " |" for row in padded[1:])
    return lines


def docx_to_markdown(path: Path) -> str:
    """把 docx 正文转成 Markdown；封面等无标题段落由切分阶段自然丢弃。"""
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    body = root.find(f"{WORD_NS}body")
    if body is None:
        raise ValueError(f"docx 缺少正文：{path.name}")

    lines: list[str] = []
    for child in body:
        if child.tag == f"{WORD_NS}p":
            text = _paragraph_text(child)
            if not text:
                continue
            style = _paragraph_style(child)
            level = HEADING_STYLES.get(style)
            if level:
                lines.append(f"{'#' * (level + HEADING_OFFSET)} {text}")
                continue
            lines.append(f"{_list_marker(child, style)}{text}")
        elif child.tag == f"{WORD_NS}tbl":
            lines.extend(_table_to_markdown(child))
            lines.append("")
    return "\n".join(lines)


def build_markdown(source: Path, title: str) -> str:
    suffix = source.suffix.lower()
    if suffix == ".docx":
        body = docx_to_markdown(source)
    elif suffix in {".md", ".txt"}:
        body = source.read_text(encoding="utf-8")
    else:
        raise SystemExit(f"暂不支持 {suffix}，目前支持 .docx / .md / .txt")
    if not body.lstrip().startswith("# "):
        body = f"# {title}\n\n{body}"
    return body.strip() + "\n"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="把单份本地文档导入当前知识库")
    parser.add_argument("--file", required=True, type=Path, help="待导入文件（.docx / .md / .txt）")
    parser.add_argument("--out", type=Path, help="把转换出的 Markdown 写到该路径存档")
    parser.add_argument("--document-id", help="语料内的文档编号，例如 V3；默认取文件名")
    parser.add_argument("--title", help="文档标题；默认取文件名")
    parser.add_argument(
        "--document-version",
        help="资料版本，例如 V1.0 / 第3册；默认从标题和文件名里抽，抽不到就留空",
    )
    parser.add_argument("--corpus-id", default=SMARTPV_CORPUS_ID, help="语料库标识")
    parser.add_argument("--chunk-size", type=int, default=None)
    parser.add_argument("--chunk-overlap", type=int, default=None)
    parser.add_argument("--include-restricted", action="store_true", help="连受限章节一起导入")
    parser.add_argument("--dry-run", action="store_true", help="只打印章节切分结果，不写库")
    return parser.parse_args(argv)


async def main() -> int:
    args = parse_args()
    settings = get_settings()
    args.chunk_size = settings.chunk_size if args.chunk_size is None else args.chunk_size
    args.chunk_overlap = (
        settings.chunk_overlap if args.chunk_overlap is None else args.chunk_overlap
    )
    if not 0 <= args.chunk_overlap < args.chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")
    source = args.file.expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"文件不存在：{source}")

    title = args.title or source.stem
    markdown = build_markdown(source, title)

    if args.out:
        out_path = args.out.expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown, encoding="utf-8")
        filename = out_path.name
        print(f"已写出 Markdown：{out_path}（{len(markdown)} 字符）")
    else:
        filename = f"{source.stem}.md"

    sections: list[tuple[str, str, bool]] = []
    for section_title, content in split_markdown_sections(markdown):
        restricted = any(term in section_title for term in RESTRICTED_SECTION_TERMS)
        if restricted and not args.include_restricted:
            print(f"  跳过受限章节：{section_title}")
            continue
        sections.append((section_title, content, restricted))

    print(f"来源文件 : {source.name}")
    print(f"文档标题 : {title}")
    print(f"章节数   : {len(sections)}")
    # 向量后端要和线上检索用的是同一个，否则导入的向量查不到。
    # 本机向量维度与库里已有片段不一致时这里会直接报出来。
    backend = get_embedding_backend()
    print(f"向量后端 : {backend.signature}")
    if args.dry_run:
        for section_title, content, restricted in sections:
            flag = " [受限]" if restricted else ""
            print(f"  - {section_title}（{len(content)} 字）{flag}")
        print("\n--dry-run：未写入数据库。")
        return 0

    digest = checksum(markdown.encode("utf-8"))
    document_id = args.document_id or source.stem

    await create_schema()
    async with SessionFactory() as db:
        existing = await db.scalar(select(SourceDocument).where(SourceDocument.checksum == digest))
        if existing:
            print(f"\n内容与前次导入一致（校验和 {digest[:12]}），已跳过，未产生重复片段。")
            return 0

        document = SourceDocument(filename=filename, content_type="text/markdown", checksum=digest)
        db.add(document)
        await db.flush()

        # 先把这份文档的全部片段收齐再算向量，一次交给后端分批推理。
        pending: list[tuple[dict, PageText]] = []
        for section_title, content, restricted in sections:
            metadata = KnowledgeChunk(
                document_id=document_id,
                document_title=title,
                section_title=section_title,
                page_start=None,
                page_end=None,
                source_path=Path(filename),
                content=content,
                restricted=restricted,
                document_version=args.document_version,
            ).metadata
            metadata["corpus_id"] = args.corpus_id
            metadata["embedding_signature"] = backend.signature
            pending.extend(
                (metadata, piece)
                for piece in chunk_pages(
                    [PageText(page=None, text=content)], args.chunk_size, args.chunk_overlap
                )
            )
        vectors = await backend.embed_async([piece.text for _, piece in pending])

        for position, ((metadata, piece), vector) in enumerate(zip(pending, vectors, strict=True)):
            db.add(
                DocumentChunk(
                    document_id=document.id,
                    position=position,
                    page=piece.page,
                    content=piece.text,
                    chunk_metadata=metadata,
                    embedding=vector,
                )
            )
        await db.commit()
        # 这个脚本自己写库、没走 RAGService.ingest，所以要自己通知缓存失效。
        # 漏掉这一步的表现很隐蔽：正跑着的服务在缓存 TTL（默认 5 分钟）内
        # 查不到刚导进来的内容，看起来像「导入没成功」。
        await get_retrieval_cache().invalidate()

    print(
        f"\n导入完成：新增文档 1 份（{filename}），章节 {len(sections)}，"
        f"新增片段 {len(pending)} 条，向量后端 {backend.signature}。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
