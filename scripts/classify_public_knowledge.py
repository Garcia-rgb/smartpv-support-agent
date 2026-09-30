"""将已核对的华为公开手册和 HCSA 通用片段标为普通资料。

默认只预览；带 --apply 才修改当前 DATABASE_URL 指向的资料库。
有账号、密码、客户或内部信息的课程片段保留内部级别。
"""

import argparse
import asyncio
import re
from collections import Counter
from pathlib import PurePosixPath

from sqlalchemy import select

from support_agent.db import SessionFactory
from support_agent.models import DocumentChunk, SourceDocument
from support_agent.services.cache import get_retrieval_cache

PUBLIC_MANUALS = {
    "MERC优化器用户手册.pdf",
    "iManager-NetEco1000S-Manual.pdf",
    "SUN2000-(2KTL-6KTL)-L1-User-Manual.pdf",
}
COURSE_NAME = re.compile(r"^M(?:[1-9]|1[0-4])-")
PRIVATE_CONTENT = re.compile(
    r"账号|密码|口令|密钥|验证码|手机号|客户|业主|联系人|"
    r"公司内部|内部平台|工单|\bSN-\d{4}-\d{6}\b|"
    r"\b1[3-9]\d{9}\b|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
    re.I,
)


def approved_source(filename: str) -> bool:
    name = PurePosixPath(filename.replace("\\", "/")).name
    return (
        name in PUBLIC_MANUALS
        or bool(COURSE_NAME.match(name))
        or name in {"HCSA-V2-模拟题逐题解析.md", "导读-教材包总目录与学习路径.md"}
    )


async def main(apply: bool) -> None:
    counts: Counter[str] = Counter()
    async with SessionFactory() as db:
        rows = (
            await db.execute(
                select(DocumentChunk, SourceDocument).join(
                    SourceDocument, SourceDocument.id == DocumentChunk.document_id
                )
            )
        ).all()
        for chunk, document in rows:
            if not approved_source(document.filename):
                continue
            if PRIVATE_CONTENT.search(chunk.content):
                counts["kept_private_sensitive"] += 1
                if apply and (chunk.chunk_metadata or {}).get("visibility") == "public":
                    chunk.chunk_metadata = {**(chunk.chunk_metadata or {}), "visibility": "private"}
                    counts["demoted"] += 1
                continue
            counts["approved_public"] += 1
            if apply and (chunk.chunk_metadata or {}).get("visibility") != "public":
                chunk.chunk_metadata = {**(chunk.chunk_metadata or {}), "visibility": "public"}
                counts["updated"] += 1
        if apply:
            await db.commit()
            if counts["updated"] or counts["demoted"]:
                await get_retrieval_cache().invalidate()
    print(dict(counts))
    if not apply:
        print("预览模式；添加 --apply 后才修改资料库")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(main(parser.parse_args().apply))
