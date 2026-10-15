import argparse
import asyncio
import os
from pathlib import Path

from support_agent.config import get_settings
from support_agent.db import Base, SessionFactory, create_schema, engine
from support_agent.services.rag import RAGService
from support_agent.services.semantic import get_embedding_backend


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导入本地 SmartPV Markdown 知识库")
    parser.add_argument(
        "--source",
        type=Path,
        default=os.getenv("SMARTPV_KB_PATH"),
        help="包含 index.json 和知识库分卷目录的本地路径",
    )
    parser.add_argument(
        "--include-restricted",
        action="store_true",
        help="显式导入账号密码、密码重置等受限章节",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="先清空所有表再导入。换 embedding 后端或改切块参数后必须这样重建，否则库里的"
        "旧向量和新查询向量不在一个空间（维度、模型都不同，余弦相似度会给出假分数），"
        "改切块参数时更隐蔽——校验和没变，整份文档会被跳过，看着像「导入完成」其实没重切",
    )
    # 切块参数是 `settings` 的配置项（CHUNK_SIZE / CHUNK_OVERLAP），这里额外开到命令行，
    # 是为了让对照实验不必改 .env 就能逐组重建索引。
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=None,
        help="每个检索片段的字符数，默认取配置 CHUNK_SIZE",
    )
    parser.add_argument(
        "--overlap",
        type=int,
        default=None,
        help="相邻片段的重叠字符数，默认取配置 CHUNK_OVERLAP",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    if args.source is None:
        raise SystemExit("请使用 --source 指定本地知识库路径")

    settings = get_settings()
    # 以前这里直接把 RAGService 的构造默认值（700/100）当成了配置，于是 .env 里
    # 写了 CHUNK_SIZE 也不生效——导入按 700 切、检索侧却按配置算语料统计。
    chunk_size = args.chunk_size if args.chunk_size is not None else settings.chunk_size
    overlap = args.overlap if args.overlap is not None else settings.chunk_overlap

    if not 0 <= overlap < chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")
    backend = get_embedding_backend(settings)
    print(f"向量后端：{backend.signature}")
    print(f"切块参数：chunk_size={chunk_size} overlap={overlap}")

    if args.reset:
        # 用 drop_all 而不是删文件：库文件常被正在运行的服务占着，Windows 上删不掉。
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        print("已清空原有数据（--reset）")

    await create_schema()
    async with SessionFactory() as db:
        report = await RAGService(
            db, chunk_size=chunk_size, overlap=overlap, backend=backend
        ).ingest_smartpv_corpus(
            args.source,
            include_restricted=args.include_restricted,
        )
    print(
        "导入完成："
        f"新文档 {report.documents_created}，"
        f"跳过 {report.documents_skipped}，"
        f"章节 {report.sections}，"
        f"检索片段 {report.chunks_created}"
    )


if __name__ == "__main__":
    asyncio.run(main())
