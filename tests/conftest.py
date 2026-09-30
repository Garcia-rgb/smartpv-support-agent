from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from support_agent.config import Settings, get_settings
from support_agent.db import Base, get_db
from support_agent.main import app
from support_agent.services.cache import reset_store


@pytest.fixture(autouse=True)
def hermetic_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """让测试不读开发者本机的 .env。

    否则一旦在 .env 里填了真实模型配置，``llm_enabled`` 就会变真，
    同一份代码在本机跑 pytest 和在 CI（没有 .env）跑会得到不同结果。
    所以测试期间一律不加载 .env，配置只能由用例显式传进来。

    清缓存这一步同样必要：``get_settings`` 带 lru_cache，而 ``db`` 模块在
    导入时就已经调用过它一次，那时读的还是本机 .env。不清掉的话，测试里
    的默认配置会带着开发机的 ``EMBEDDING_BACKEND=onnx`` 一起进来，
    每次建向量都去加载一次 90MB 的模型。
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    # 缓存存储同样是模块级单例，也必须清：上一个用例写进去的检索缓存和限流
    # 计数会漏到下一个用例，表现为「单独跑通过、一起跑就挂」。
    # 测试环境不配 REDIS_URL，所以拿到的是进程内实现，不涉及连接要关。
    reset_store()
    yield
    reset_store()
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def db_session(tmp_path) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        database_url="sqlite+aiosqlite:///unused.db",
        confirmation_secret="test-secret",
        max_upload_bytes=1024 * 1024,
        auth_enabled=False,
    )

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
    app.dependency_overrides.clear()
