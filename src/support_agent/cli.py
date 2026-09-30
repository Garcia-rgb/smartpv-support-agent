"""命令行入口：``smartpv-agent <command>``。

三个子命令：

* ``version`` —— 打印版本号与关键依赖版本；
* ``doctor`` —— 环境自检（配置 / 数据库 / 语料 / 向量后端 / 模型 / 缓存），
  用来回答「为什么检索结果不对」「为什么走了规则模型」「Redis 到底有没有生效」；
* ``serve`` —— 启动 HTTP 服务。

输出统一用英文：这份输出是要被贴进 issue、日志或 CI 的，ASCII 在任何控制台和代码页下都不会失真。
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import platform
from pathlib import Path
from typing import Any

from . import __version__

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"

Row = tuple[str, str, str]

# 关注这些依赖的版本：前四个决定服务能不能起，后三个决定语义检索是否可用。
TRACKED_PACKAGES = (
    "fastapi",
    "uvicorn",
    "sqlalchemy",
    "pydantic-settings",
    "redis",
    "onnxruntime",
    "tokenizers",
)

EXIT_OK = 0
EXIT_ISSUES = 1


def _installed_version(name: str) -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(name)
    except PackageNotFoundError:
        return "not installed"


async def _database_row(settings: Any) -> Row:
    """连一次库并数一下语料规模。

    「连不上」和「连上了但还没导语料」是两种不同的状态，前者是 WARN（还没准备好），
    后者是 OK 但数字为 0——把两者分开，才能一眼看出该去修配置还是该去导语料。
    """
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import create_async_engine

    from .models import DocumentChunk, SourceDocument

    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            documents = await connection.scalar(select(func.count()).select_from(SourceDocument))
            chunks = await connection.scalar(select(func.count()).select_from(DocumentChunk))
    except Exception as exc:  # 文件不存在、表未建、PG 没起，对自检来说都是「库还没准备好」
        return "database", WARN, f"{settings.database_url} | {type(exc).__name__}: {exc}"
    finally:
        await engine.dispose()
    return "database", OK, f"{settings.database_url} | {documents} documents / {chunks} chunks"


async def _embedding_row(settings: Any, deep: bool) -> Row:
    """检查向量后端。

    ``onnx`` 是唯一会「配置写了但跑不起来」的后端：依赖和模型目录都可能缺，
    所以这里逐项检查，并且只有在 ``--deep`` 时才真的加载模型编码一句话。
    """
    backend = (settings.embedding_backend or "hash").lower()
    dimension = settings.vector_dimension

    if backend == "hash":
        return "embedding", OK, f"hash | dimension={dimension} | zero-dependency fallback"

    if backend != "onnx":
        return "embedding", FAIL, f"unknown backend {backend!r}, expected 'hash' or 'onnx'"

    missing = [
        name for name in ("onnxruntime", "tokenizers") if importlib.util.find_spec(name) is None
    ]
    if missing:
        return (
            "embedding",
            FAIL,
            f"onnx | missing dependency: {', '.join(missing)} | pip install -e \".[semantic]\"",
        )

    if not settings.embedding_model_path:
        return "embedding", FAIL, "onnx | EMBEDDING_MODEL_PATH is not set"

    model_path = Path(settings.embedding_model_path)
    if not (model_path / "tokenizer.json").is_file() or not (
        model_path / "onnx" / "model.onnx"
    ).is_file():
        return (
            "embedding",
            FAIL,
            f"onnx | tokenizer.json / onnx/model.onnx not found in {model_path}",
        )

    if not deep:
        return (
            "embedding",
            OK,
            f"onnx | dimension={dimension} | model={model_path} | files present"
            " (use --deep to load)",
        )

    from .services.semantic import build_embedding_backend

    try:
        built = build_embedding_backend(settings)
        vectors = await built.embed_async(["self check"])
    except Exception as exc:
        return "embedding", FAIL, f"onnx | failed to load model: {type(exc).__name__}: {exc}"

    produced = len(vectors[0]) if vectors else 0
    if produced != dimension:
        return (
            "embedding",
            FAIL,
            f"onnx | model outputs {produced} dims but EMBEDDING_DIMENSION is {dimension}",
        )
    return "embedding", OK, f"onnx | loaded | dimension={produced} | signature={built.signature}"


def _model_row(settings: Any) -> Row:
    """模型配置。密钥只报「已配置」，不打印内容。"""
    if not settings.allow_remote_llm:
        return (
            "model",
            WARN,
            "remote model disabled (ALLOW_REMOTE_LLM=false) -> local rule-based model",
        )
    if not settings.llm_enabled:
        return (
            "model",
            WARN,
            "not configured (LLM_BASE_URL / LLM_MODEL / LLM_API_KEY)"
            " -> /chat uses the local rule-based model",
        )
    return "model", OK, f"base_url={settings.llm_base_url} | model={settings.llm_model} | key=***"


async def _cache_row(settings: Any) -> Row:
    """真实交互一次再看状态，而不是只看配置。

    配了 ``REDIS_URL`` 但连不上时，``ResilientStore`` 要先被用过一次才会打开熔断，
    describe() 才会如实报出 ``degraded``；不主动探测的话这里会显示成一切正常。
    """
    from .services.cache import close_store, get_store

    try:
        store = get_store(settings)
        await store.get("__doctor_probe__")
        return "cache", OK, json.dumps(store.describe(), ensure_ascii=False)
    except Exception as exc:
        return "cache", FAIL, f"{type(exc).__name__}: {exc}"
    finally:
        await close_store()


def _retrieval_row(settings: Any) -> Row:
    from .services.rag import CORPUS_MISSING_CEILING, CORPUS_MISSING_MIN_CHUNKS

    detail = (
        f"top_k={settings.retrieval_top_k} | min_score={settings.retrieval_min_score}"
        f" | corpus_id={settings.retrieval_corpus_id or '(all)'}"
        f" | refusal gate: phrase-missing >= {CORPUS_MISSING_CEILING}"
        f" once corpus has >= {CORPUS_MISSING_MIN_CHUNKS} chunks"
    )
    return "retrieval", OK, detail


async def _collect_rows(settings: Any, deep: bool) -> list[Row]:
    rows: list[Row] = [("configuration", OK, f"app_env={settings.app_env}")]
    rows.append(await _database_row(settings))
    rows.append(await _embedding_row(settings, deep))
    rows.append(_model_row(settings))
    rows.append(_retrieval_row(settings))
    rows.append(await _cache_row(settings))
    rows.append(
        (
            "rate limit",
            OK,
            f"{settings.rate_limit_requests} requests / {settings.rate_limit_window_seconds}s"
            " per user (0 disables it)",
        )
    )
    return rows


def _render(rows: list[Row], strict: bool = False) -> int:
    """打印自检结果并给出退出码。

    警告与失败必须分开：没配远程模型是**受支持的默认模式**（退回本地规则模型），
    新克隆的仓库和 CI 都长这样。若把警告也算作失败，``doctor`` 就没法像 README
    承诺的那样「直接串进 CI」——那条命令每次都会红。所以默认只有 ``FAIL`` 才返回
    非零；需要「全绿才算过」的部署门禁加 ``--strict``，把警告一并升级为失败。
    """
    width = max(len(name) for name, _, _ in rows)
    for name, status, detail in rows:
        print(f"[{status:<4}] {name:<{width}}  {detail}")
    failures = [row for row in rows if row[1] == FAIL]
    warnings = [row for row in rows if row[1] == WARN]
    print()
    if not failures and not warnings:
        print("result: all checks passed")
        return EXIT_OK
    if failures:
        print(f"result: {len(failures)} failure(s), {len(warnings)} warning(s)")
        return EXIT_ISSUES
    if strict:
        print(f"result: {len(warnings)} warning(s) promoted to failures by --strict")
        return EXIT_ISSUES
    print(f"result: {len(warnings)} warning(s), no failures")
    return EXIT_OK


def cmd_version(_: argparse.Namespace) -> int:
    print(f"SmartPV Support Agent {__version__}")
    print(f"{'python':<18}{platform.python_version()} ({platform.python_implementation()})")
    print(f"{'platform':<18}{platform.platform()}")
    print(f"{'package':<18}{Path(__file__).resolve().parent}")
    print()
    for name in TRACKED_PACKAGES:
        print(f"{name:<18}{_installed_version(name)}")
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    from .config import get_settings

    settings = get_settings()
    print(f"SmartPV Support Agent {__version__} | doctor")
    print()
    rows = asyncio.run(_collect_rows(settings, args.deep))
    return _render(rows, strict=args.strict)


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "support_agent.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smartpv-agent",
        description="Photovoltaic plant technical support agent: RAG, tool calling, "
        "human confirmation and offline evaluation.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("version", help="print version and key dependency versions")

    doctor = subparsers.add_parser("doctor", help="check configuration and runtime dependencies")
    doctor.add_argument(
        "--deep",
        action="store_true",
        help="also load the embedding model and run one embedding (slower)",
    )
    doctor.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as failures (for deploy gates that require a fully green run)",
    )

    serve = subparsers.add_parser("serve", help="start the HTTP service")
    serve.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8000, help="bind port (default: 8000)")
    serve.add_argument("--reload", action="store_true", help="reload on code changes (development)")
    serve.add_argument("--log-level", default="info", help="uvicorn log level (default: info)")
    return parser


HANDLERS = {
    "version": cmd_version,
    "doctor": cmd_doctor,
    "serve": cmd_serve,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return EXIT_OK
    return HANDLERS[args.command](args)
