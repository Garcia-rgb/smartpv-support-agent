import hashlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader


@dataclass(frozen=True)
class PageText:
    """从源文件中提取的一页文本；普通文本文件没有页码。"""

    page: int | None
    text: str


SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf"}


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_pages(filename: str, data: bytes) -> list[PageText]:
    """根据文件扩展名提取 PDF、Markdown 或纯文本内容。"""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError("仅支持 .txt、.md 和 .pdf 文件")
    if suffix == ".pdf":
        reader = PdfReader(BytesIO(data))
        return [
            PageText(page=index + 1, text=page.extract_text() or "")
            for index, page in enumerate(reader.pages)
        ]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("文本文件必须使用 UTF-8 编码") from exc
    return [PageText(page=None, text=text)]


def chunk_pages(
    pages: list[PageText], chunk_size: int | None = None, overlap: int | None = None
) -> list[PageText]:
    """按字符数切分页面，并保留相邻片段间的重叠内容。"""
    from ..config import get_settings

    settings = get_settings()
    chunk_size = settings.chunk_size if chunk_size is None else chunk_size
    overlap = settings.chunk_overlap if overlap is None else overlap
    if chunk_size <= overlap or overlap < 0:
        raise ValueError("chunk_size 必须大于 overlap")
    chunks: list[PageText] = []
    # 步长小于片段长度，重叠部分可避免关键信息被切在两个片段之间。
    step = chunk_size - overlap
    for page in pages:
        normalized = "\n".join(line.strip() for line in page.text.splitlines() if line.strip())
        for start in range(0, len(normalized), step):
            content = normalized[start : start + chunk_size].strip()
            if content:
                chunks.append(PageText(page=page.page, text=content))
            if start + chunk_size >= len(normalized):
                break
    return chunks
