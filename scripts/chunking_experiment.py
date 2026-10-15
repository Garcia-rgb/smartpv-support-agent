"""切块参数对照实验：同一份语料、同一份评测集，只改 chunk_size/overlap 三组。

为什么要有这个脚本
------------------

「切块用 700/100」以前是拍的。要把它变成站得住的默认值，
至少需要一次对照：同一批文档、同一套检索参数、同一份标注了应命中关键词的问题，
只换切块粒度，看命中率怎么变。

实验口径
--------

- 每组参数用**独立的一次性数据库**（`.chunking-experiment/<size>-<overlap>.db`），
  组间不共享任何索引；跑完就留着，方便事后翻查，不影响开发库。
- 语料 = 分卷目录（19 份）加上 `--extra` 指定的零散文档（默认第3册），
  与开发库一致，这样 700/100 组可以拿去和开发库现状对一遍，验证实验口径没问题。
- 检索走线上同一条路径（`SupportAgent.retrieve`：同语料、同阈值、同去重、同 top_k）。
- 评测集要求每行带 `expect_keyword`（或 `expected_keywords`），
  命中判据是「该关键词出现在返回的第几个片段里」：
  出现在第 1 条即 hit@1，前 5 条都没有就算 miss。MRR 取首个命中名次的倒数。
- 只统计带关键词标注的样本；没有标注的行（如纯跑题问题）不进分母。

用法
----

    python scripts/chunking_experiment.py --source "D:\\资料汇总"
    python scripts/chunking_experiment.py --configs "300/50,1200/150" --source "..."
    python scripts/chunking_experiment.py --report        # 只汇总已有的 JSON

脚本会为每组参数拉起一个子进程（环境变量必须在导入 `support_agent` 之前设好，
否则 `db.py` 导入时就已经把 engine 绑到默认库上了）。
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_CONFIGS = "300/50,700/100,1200/150"
DEFAULT_DATASET = Path(r"D:\资料汇总\拓展资料\检索质量评测.jsonl")
# 分卷目录之外的零散文档。开发库里这份是单独导入的，实验里补上它，
# 三组参数的语料才和线上一致（也才能拿 700/100 组去复现开发库现状）。
DEFAULT_EXTRA = (Path(r"D:\资料汇总\拓展资料\第3册-交直流屏柜原理.md"),)
WORKDIR = Path(".chunking-experiment")
# 检索 top_k 在这里写死为线上默认值，避免不同 top_k 之间没法比。



def load_dataset(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        keywords = raw.get("expect_keyword") or raw.get("expected_keywords") or []
        if isinstance(keywords, str):
            keywords = [keywords]
        rows.append({"question": raw["question"], "keywords": list(keywords)})
    return rows


def parse_configs(text: str) -> list[tuple[int, int]]:
    configs = []
    for item in text.split(","):
        size, overlap = item.strip().split("/")
        configs.append((int(size), int(overlap)))
    return configs


async def _worker(
    source: Path,
    dataset: Path,
    chunk_size: int,
    overlap: int,
    out: Path,
    extras: tuple[Path, ...] = (),
) -> None:
    """在子进程里跑一组参数：清空 → 导入 → 逐条检索 → 落 JSON。"""
    # 导入必须放在环境变量设好之后（见模块 docstring）。
    from support_agent.desktop_runtime import prefer_python_sqlalchemy
    prefer_python_sqlalchemy()
    from sqlalchemy import func, select

    from support_agent.config import Settings
    from support_agent.db import Base, SessionFactory, create_schema, engine
    from support_agent.models import DocumentChunk
    from support_agent.services.agent import SupportAgent
    from support_agent.services.rag import RAGService
    from support_agent.services.semantic import get_embedding_backend

    backend = get_embedding_backend()
    print(f"[{chunk_size}/{overlap}] 向量后端 {backend.signature}")

    # 一次性库：进程内先 drop 再建，避免上一轮残留。
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await create_schema()

    settings = Settings()
    rows = load_dataset(dataset)
    extras_used = 0

    async with SessionFactory() as db:
        rag = RAGService(db, chunk_size=chunk_size, overlap=overlap, backend=backend)
        report = await rag.ingest_smartpv_corpus(source)
        print(
            f"[{chunk_size}/{overlap}] 导入 文档 {report.documents_created} / "
            f"片段 {report.chunks_created}"
        )
        for extra in extras:
            if not extra.exists():
                print(f"[{chunk_size}/{overlap}] 附加文档不存在，跳过：{extra}")
                continue
            _, extra_chunks, skipped = await rag.ingest(
                extra.name, "text/markdown", extra.read_bytes()
            )
            extras_used += 1
            print(
                f"[{chunk_size}/{overlap}] 附加 {extra.name} -> {extra_chunks} 片段"
                f"（已存在跳过={skipped}）"
            )

        agent = SupportAgent(db, settings)
        details = []
        for row in rows:
            if not row["keywords"]:
                continue
            hits = await agent.retrieve(row["question"])
            contents = [hit.chunk.content for hit in hits]
            rank = 0
            for index, content in enumerate(contents, start=1):
                if any(word.lower() in content.lower() for word in row["keywords"]):
                    rank = index
                    break
            details.append(
                {
                    "question": row["question"],
                    "keywords": row["keywords"],
                    "rank": rank,
                    "hits": len(hits),
                    "top_score": round(hits[0].score, 4) if hits else 0.0,
                }
            )

        # 用库里的实际数据核对导入结果，而不是只信导入报告里的数字。
        chunk_count = await db.scalar(select(func.count()).select_from(DocumentChunk)) or 0
        avg_chars = await db.scalar(select(func.avg(func.length(DocumentChunk.content)))) or 0.0

    scored = details
    total = len(scored)
    result = {
        "chunk_size": chunk_size,
        "overlap": overlap,
        "backend": backend.signature,
        "top_k": settings.retrieval_top_k,
        "tuning": settings.tuning_snapshot(),
        "suite_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "documents": report.documents_created + extras_used,
        "chunks": int(chunk_count),
        "avg_chunk_chars": round(float(avg_chars), 1),
        "samples": total,
        "hit_at_1": round(sum(1 for item in scored if item["rank"] == 1) / total, 4),
        "hit_at_3": round(sum(1 for item in scored if 0 < item["rank"] <= 3) / total, 4),
        "hit_at_5": round(sum(1 for item in scored if 0 < item["rank"] <= 5) / total, 4),
        "mrr": round(sum(1 / item["rank"] for item in scored if item["rank"]) / total, 4),
        "empty_results": sum(1 for item in scored if item["hits"] == 0),
        "details": scored,
    }
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[{chunk_size}/{overlap}] 片段 {result['chunks']} | "
        f"hit@1 {result['hit_at_1']:.3f} | hit@3 {result['hit_at_3']:.3f} | "
        f"hit@5 {result['hit_at_5']:.3f} | MRR {result['mrr']:.3f} | "
        f"空结果 {result['empty_results']}"
    )


def run_config(
    source: Path, dataset: Path, chunk_size: int, overlap: int, extras: tuple[Path, ...]
) -> None:
    """为单组参数拉起子进程：DATABASE_URL / CHUNK_SIZE 必须在导入前进环境。"""
    WORKDIR.mkdir(exist_ok=True)
    db_path = WORKDIR / f"{chunk_size}-{overlap}.db"
    db_path.unlink(missing_ok=True)
    out = WORKDIR / f"{chunk_size}-{overlap}.json"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///./{db_path.as_posix()}"
    env["CHUNK_SIZE"] = str(chunk_size)
    env["CHUNK_OVERLAP"] = str(overlap)
    # 强制走进程内缓存：对照实验不该让上一组的缓存影响下一组。
    env["REDIS_URL"] = ""
    command = [
        sys.executable,
        str(Path(__file__)),
        "--single",
        str(chunk_size),
        str(overlap),
        "--source",
        str(source),
        "--dataset",
        str(dataset),
        "--out",
        str(out),
    ]
    for extra in extras:
        command.extend(["--extra", str(extra)])
    subprocess.run(command, env=env, check=True)


def report() -> None:
    files = sorted(WORKDIR.glob("*.json"), key=lambda p: json.loads(p.read_text())["chunk_size"])
    if not files:
        raise SystemExit("还没有结果，先跑一次实验")
    rows = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    header = (
        f"{'chunk/overlap':>14} | {'片段数':>6} | {'均长':>7} | {'hit@1':>6} | "
        f"{'hit@3':>6} | {'hit@5':>6} | {'MRR':>6} | {'空结果':>6}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        label = f"{row['chunk_size']}/{row['overlap']}"
        print(
            f"{label:>14} | {row['chunks']:>6} | {row['avg_chunk_chars']:>7.0f} | "
            f"{row['hit_at_1']:>6.3f} | "
            f"{row['hit_at_3']:>6.3f} | {row['hit_at_5']:>6.3f} | {row['mrr']:>6.3f} | "
            f"{row['empty_results']:>6}"
        )
    print()
    print(f"样本数 {rows[0]['samples']}（带 expect_keyword 的条目），top_k={rows[0]['top_k']}")
    print(f"向量后端 {rows[0]['backend']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="切块参数对照实验")
    parser.add_argument("--source", type=Path, default=Path(r"D:\资料汇总"))
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--configs", default=DEFAULT_CONFIGS)
    parser.add_argument(
        "--extra",
        type=Path,
        action="append",
        default=None,
        help="分卷目录之外的附加文档，可重复；默认带上第3册",
    )
    parser.add_argument("--report", action="store_true", help="只汇总已有结果")
    parser.add_argument("--single", nargs=2, metavar=("CHUNK_SIZE", "OVERLAP"), default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    extras = tuple(args.extra) if args.extra else DEFAULT_EXTRA

    if args.report:
        report()
        return

    if args.single:
        import asyncio

        size, overlap = int(args.single[0]), int(args.single[1])
        out = args.out or (WORKDIR / f"{size}-{overlap}.json")
        asyncio.run(_worker(args.source, args.dataset, size, overlap, out, extras))
        return

    for size, overlap in parse_configs(args.configs):
        print(f"=== 切块 {size}/{overlap} ===")
        run_config(args.source, args.dataset, size, overlap, extras)
    print()
    report()


if __name__ == "__main__":
    main()
