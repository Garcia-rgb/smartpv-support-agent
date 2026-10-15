import os
import subprocess
import sys
from pathlib import Path


def test_desktop_uses_python_sqlalchemy_and_sqlite_transactions():
    root = Path(__file__).parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(str(root / p) for p in ("src", ".localdeps"))
    env["PYTHONNOUSERSITE"] = "1"
    code = '''
import asyncio
from support_agent.desktop_runtime import prefer_python_sqlalchemy, SQLAlchemyPythonFinder
prefer_python_sqlalchemy()
prefer_python_sqlalchemy()
import sys
assert sum(isinstance(f, SQLAlchemyPythonFinder) for f in sys.meta_path) == 1
from sqlalchemy.util._has_cython import HAS_CYEXTENSION
assert not HAS_CYEXTENSION
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
async def check():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as db:
        await db.execute(text("CREATE TABLE checks (value INTEGER)"))
        await db.execute(text("INSERT INTO checks VALUES (42)"))
    async with engine.connect() as db:
        assert await db.scalar(text("SELECT value FROM checks")) == 42
    await engine.dispose()
asyncio.run(check())
print("python fallback and committed SQLite transaction passed")
'''
    result = subprocess.run([sys.executable, "-c", code], env=env, cwd=root,
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
    assert "transaction passed" in result.stdout
