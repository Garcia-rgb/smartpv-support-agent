import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from support_agent.models import DocumentChunk
from support_agent.services.knowledge_base import (
    load_smartpv_corpus,
    split_markdown_sections,
)
from support_agent.services.rag import RAGService


def test_split_markdown_sections_uses_h2_boundaries() -> None:
    text = "# M1 示例\n\n## 1.1 MPPT\n正文一\n\n## 1.2 防孤岛\n正文二"

    sections = split_markdown_sections(text)

    assert [title for title, _ in sections] == ["1.1 MPPT", "1.2 防孤岛"]
    assert "正文一" in sections[0][1]


def test_split_markdown_sections_supports_standalone_bold_headings() -> None:
    text = "# M9 示例\n\n**升级/日志**\n正文一\n\n**密码重置**\n正文二"

    sections = split_markdown_sections(text)

    assert [title for title, _ in sections] == ["升级/日志", "密码重置"]


def test_load_corpus_adds_metadata_and_excludes_restricted_by_default(tmp_path: Path) -> None:
    split_dir = tmp_path / "HCSA-SmartPV-V2.0-知识库分卷"
    split_dir.mkdir()
    (split_dir / "M1-基础.md").write_text(
        "# M1 基础\n\n## 1.1 MPPT\n最大功率点跟踪。", encoding="utf-8"
    )
    (split_dir / "附录A-速查.md").write_text(
        "# 附录 A\n\n## A1 参数\n普通参数。\n\n## A4 账号、密码与默认值\n秘密。",
        encoding="utf-8",
    )
    index = {
        "modules": [{"id": "M1", "title": "基础", "pages": "p1–36"}],
        "appendices": [{"id": "A", "title": "速查"}],
    }
    (tmp_path / "HCSA-SmartPV-V2.0-index.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8"
    )

    chunks = load_smartpv_corpus(tmp_path)

    assert [chunk.section_title for chunk in chunks] == ["1.1 MPPT", "A1 参数"]
    assert chunks[0].metadata["document_id"] == "M1"
    assert chunks[0].metadata["page_start"] == 1
    assert chunks[0].metadata["page_end"] == 36
    assert chunks[0].metadata["visibility"] == "local_only"


def test_load_corpus_can_include_restricted_sections(tmp_path: Path) -> None:
    split_dir = tmp_path / "HCSA-SmartPV-V2.0-知识库分卷"
    split_dir.mkdir()
    (split_dir / "附录A-速查.md").write_text(
        "# 附录 A\n\n## A4 账号、密码与默认值\n秘密。", encoding="utf-8"
    )
    index = {"modules": [], "appendices": [{"id": "A", "title": "速查"}]}
    (tmp_path / "HCSA-SmartPV-V2.0-index.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8"
    )

    chunks = load_smartpv_corpus(tmp_path, include_restricted=True)

    assert len(chunks) == 1
    assert chunks[0].restricted is True


def test_load_corpus_rejects_missing_split_file(tmp_path: Path) -> None:
    (tmp_path / "HCSA-SmartPV-V2.0-知识库分卷").mkdir()
    index = {"modules": [{"id": "M1", "title": "基础"}], "appendices": []}
    (tmp_path / "HCSA-SmartPV-V2.0-index.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="M1 应对应一份分卷"):
        load_smartpv_corpus(tmp_path)


async def test_ingest_corpus_stores_section_metadata(
    tmp_path: Path, db_session: AsyncSession
) -> None:
    split_dir = tmp_path / "HCSA-SmartPV-V2.0-知识库分卷"
    split_dir.mkdir()
    (split_dir / "M1-基础.md").write_text(
        "# M1 基础\n\n## 1.1 MPPT\n最大功率点跟踪可以提高发电效率。",
        encoding="utf-8",
    )
    index = {
        "modules": [{"id": "M1", "title": "基础", "pages": "p1–36"}],
        "appendices": [],
    }
    (tmp_path / "HCSA-SmartPV-V2.0-index.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8"
    )

    rag = RAGService(db_session, chunk_size=40, overlap=5)
    report = await rag.ingest_smartpv_corpus(tmp_path)
    stored = (await db_session.scalars(select(DocumentChunk))).one()

    assert report.documents_created == 1
    assert report.sections == 1
    assert report.chunks_created == 1
    assert stored.chunk_metadata["section_title"] == "1.1 MPPT"
    assert stored.chunk_metadata["visibility"] == "private"
    assert stored.chunk_metadata["source_file"] == "M1-基础.md"
    assert stored.chunk_metadata["corpus_id"] == "smartpv_v2"

    await rag.ingest("extra.md", "text/markdown", "绝缘阻抗低时先检查保护地线。".encode())
    hits = await rag.search("最大功率点", corpus_id="smartpv_v2")
    assert hits
    assert all(hit.chunk.chunk_metadata["corpus_id"] == "smartpv_v2" for hit in hits)
    assert await rag.search("保护地线", corpus_id="missing_corpus") == []
