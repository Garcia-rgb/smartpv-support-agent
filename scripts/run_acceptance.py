"""Run synthetic isolated acceptance or explicitly select a local real-case suite."""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / ".localdeps")]


async def run(args):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from support_agent import __version__
    from support_agent.config import Settings, get_settings
    from support_agent.db import Base
    from support_agent.services.acceptance import run_acceptance
    from support_agent.services.rag import RAGService

    if args.remote and not args.allow_external_data:
        raise ValueError("远程验收会外发案例问题和命中资料，请明确指定 --allow-external-data")
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    settings = get_settings() if args.live_library else Settings(
        _env_file=None, privacy_routing_enabled=True, embedding_backend="hash"
    )
    with tempfile.TemporaryDirectory() as temp:
        url = settings.database_url if args.live_library else (
            "sqlite+aiosqlite:///" + str(Path(temp) / "acceptance.db")
        )
        engine = create_async_engine(url)
        if not args.live_library:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            if not args.live_library:
                rag = RAGService(db)
                for document in suite.get("documents", []):
                    await rag.ingest(document["filename"], "text/markdown",
                                     document["text"].encode(), visibility="public")
            report = await run_acceptance(db, suite["cases"], settings, remote=args.remote)
        await engine.dispose()
    report["version"] = __version__
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Acceptance {report['passed']}/{report['total']} [{report['mode']}]: {args.output}")
    return int(report["passed"] != report["total"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path,
                        default=(ROOT / "tests/fixtures/answer_acceptance.json") if (
                            ROOT / "tests/fixtures/answer_acceptance.json"
                        ).exists() else ROOT / "scripts/fixtures/answer_acceptance.json")
    parser.add_argument("--output", type=Path, default=ROOT / "本地文档/验收报告/latest.json")
    parser.add_argument("--live-library", action="store_true")
    parser.add_argument("--remote", action="store_true")
    parser.add_argument("--allow-external-data", action="store_true")
    args = parser.parse_args()
    os.chdir(Path(os.environ.get("SUPPORT_AGENT_DATA_DIR", ROOT)))
    raise SystemExit(asyncio.run(run(args)))
