"""Offline install gate: imports, static assets, database and image dependencies."""

import argparse
import asyncio
import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(Path(os.environ.get("SUPPORT_AGENT_DATA_DIR", ROOT)))
sys.path[:0] = [str(ROOT / "src"), str(ROOT / ".localdeps")]


async def probe(expected_version=None):
    for name in ("onnxruntime", "rapidocr", "tokenizers", "webview"):
        print("Checking runtime:", name, flush=True)
        importlib.import_module(name)

    from support_agent import __version__
    from support_agent.db import create_schema, engine
    print("Checking application", flush=True)
    from support_agent.main import STATIC_DIR
    if expected_version and __version__ != expected_version:
        raise RuntimeError("程序版本与安装包记录不一致")
    if STATIC_DIR is None:
        raise RuntimeError("客户端页面缺失")
    await create_schema()
    await engine.dispose()
    print("release probe passed:", __version__)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-version")
    asyncio.run(probe(parser.parse_args().expected_version))
