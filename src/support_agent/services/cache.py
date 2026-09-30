"""检索缓存与请求限流的存储层。

两件事共用一个 key-value 抽象，因为它们的降级策略是同一套判断：
Redis 不可用时，缓存退化成「每次都直通」（只损失性能，不损失正确性），
限流退化成「进程内计数」（保护变弱但不会消失）。

一条纪律：**缓存服务不可用绝不能让接口不可用**。本机开发时经常只配了
`REDIS_URL` 却没启服务，如果每个请求都先去连一次再等超时，接口会被拖垮。
所以这里做了熔断——失败一次后冷却期内直接走兜底，不再尝试。
"""

from __future__ import annotations

import abc
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from sqlalchemy import select

from ..config import Settings, get_settings
from ..models import DocumentChunk, SourceDocument

logger = logging.getLogger("support_agent.cache")

# 语料代次：语料一变就 +1，检索缓存的 key 里带着它，旧 key 靠 TTL 自然清掉。
GENERATION_KEY = "support_agent:corpus:generation"


class KeyValueStore(abc.ABC):
    """存储原语：只提供检索缓存和限流真正需要的四种操作。

    刻意不做成 Redis 客户端的全量透传——上层用不到的能力留在这里，
    只会让人猜「这个项目到底靠 Redis 做了多少事」。
    """

    name = "base"

    @abc.abstractmethod
    async def get(self, key: str) -> str | None:
        """读一个值，不存在或已过期返回 None。"""

    @abc.abstractmethod
    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        """写一个值并设置有效期。缓存的值必须过期，否则语料更新后会一直读到旧结果。"""

    @abc.abstractmethod
    async def incr(self, key: str, ttl_seconds: int | None = None) -> tuple[int, int | None]:
        """自增并返回 ``(新值, 剩余有效期秒数)``。

        ``ttl_seconds`` 为 None 表示不过期（语料代次用），
        否则只在键首次创建时设置有效期（限流用——计数窗口内不能被续期）。
        """

    async def close(self) -> None:  # noqa: B027 - 有意做成可选覆盖：进程内实现没有连接要关
        """释放连接。默认什么都不做，只有需要关连接的后端才覆盖它。"""

    def describe(self) -> dict[str, Any]:
        """给 /health 用的状态描述。"""
        return {"backend": self.name}


class MemoryStore(KeyValueStore):
    """进程内实现：本地开发、CI 和单元测试的默认路径。

    多进程部署时每个进程各有一份，所以缓存命中率会下降、限流额度也会
    按进程数放大——它只是「没有 Redis 时也不要退化成什么都没有」的兜底，
    不是 Redis 的等价替代。
    """

    name = "memory"

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        # 时钟可以注入：过期行为要靠时间流逝才能验证，而「睡够 TTL」会让
        # 测试变慢又脆弱。默认就是单调时钟，生产路径没有区别。
        self._clock = clock
        self._values: dict[str, tuple[str, float | None]] = {}

    def _live(self, key: str) -> str | None:
        entry = self._values.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and expires_at <= self._clock():
            # 惰性过期：读的时候顺手清掉，不需要后台线程定时扫。
            self._values.pop(key, None)
            return None
        return value

    async def get(self, key: str) -> str | None:
        return self._live(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        expires_at = self._clock() + ttl_seconds if ttl_seconds > 0 else None
        self._values[key] = (value, expires_at)

    async def incr(self, key: str, ttl_seconds: int | None = None) -> tuple[int, int | None]:
        current = self._live(key)
        if current is None:
            count = 1
            expires_at = None if ttl_seconds is None else self._clock() + ttl_seconds
        else:
            # 已存在的键不续期：限流窗口内必须到点就重置。
            count = int(current) + 1
            expires_at = self._values[key][1]
        self._values[key] = (str(count), expires_at)
        if expires_at is None:
            return count, None
        return count, max(0, int(expires_at - self._clock()))


# 自增 + 首次设过期必须原子完成，否则并发下会出现「键已创建但过期没设上」，
# 计数永远不清零。用一段 Lua 把它变成一次往返。
_INCR_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if tonumber(ARGV[1]) > 0 and count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return {count, redis.call('TTL', KEYS[1])}
"""


class RedisStore(KeyValueStore):
    """Redis 实现。

    超时必须设短：这个存储对正确性不是必需的，让一个慢 Redis 拖住整个请求
    比直接降级到内存更糟。
    """

    name = "redis"

    def __init__(self, client: Any) -> None:
        self._client = client
        self._incr = client.register_script(_INCR_SCRIPT)

    @classmethod
    def from_url(cls, url: str) -> RedisStore:
        import redis.asyncio as redis

        client = redis.from_url(
            url,
            decode_responses=True,
            socket_timeout=1.0,
            socket_connect_timeout=1.0,
        )
        return cls(client)

    async def get(self, key: str) -> str | None:
        return await self._client.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        if ttl_seconds > 0:
            await self._client.set(key, value, ex=ttl_seconds)
        else:
            # Redis 不接受 ex=0，但也不能因此把它当成「1 秒后过期」——
            # 那和进程内实现的「不过期」语义不一致，换存储会悄悄改变行为。
            await self._client.set(key, value)

    async def incr(self, key: str, ttl_seconds: int | None = None) -> tuple[int, int | None]:
        count, ttl = await self._incr(keys=[key], args=[ttl_seconds or 0])
        return int(count), (None if int(ttl) < 0 else int(ttl))

    async def close(self) -> None:
        await self._client.aclose()


class ResilientStore(KeyValueStore):
    """主存储故障时降级到本地兜底，并记住自己正在降级。

    为什么需要熔断：Redis 挂掉时，每个请求都去连一次会各吃一遍连接超时，
    接口会从「缓存失效」变成「整体变慢」。所以失败一次就打开熔断，
    冷却期内直接走兜底；冷却结束再试一次，成功就退出降级。
    """

    name = "resilient"

    def __init__(
        self, primary: KeyValueStore, fallback: KeyValueStore, *, cooldown_seconds: int = 30
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._cooldown = cooldown_seconds
        self._open_until = 0.0
        self._degraded = False

    @property
    def degraded(self) -> bool:
        return self._degraded

    @property
    def active_name(self) -> str:
        return self._fallback.name if self._degraded else self._primary.name

    def describe(self) -> dict[str, Any]:
        return {
            "backend": self.active_name,
            "primary": self._primary.name,
            "degraded": self._degraded,
        }

    def _reachable(self) -> bool:
        return time.monotonic() >= self._open_until

    def _succeeded(self) -> None:
        if self._degraded:
            logger.warning("主存储已恢复，退出降级状态")
        self._degraded = False
        self._open_until = 0.0

    def _failed(self, exc: Exception) -> None:
        self._open_until = time.monotonic() + self._cooldown
        if not self._degraded:
            # 只在状态翻转时告警一次，否则每冷却一轮就刷一条重复日志。
            logger.warning(
                "主存储不可用（%s: %s），已降级到进程内实现；%d 秒内不再尝试。"
                "多进程部署下此时缓存命中率会下降、限流额度按进程数放大",
                type(exc).__name__,
                exc,
                self._cooldown,
            )
        self._degraded = True

    async def get(self, key: str) -> str | None:
        if self._reachable():
            try:
                value = await self._primary.get(key)
                self._succeeded()
                return value
            except Exception as exc:  # noqa: BLE001 - 任何连接/协议错误都应降级
                self._failed(exc)
        return await self._fallback.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        if self._reachable():
            try:
                await self._primary.set(key, value, ttl_seconds)
                self._succeeded()
                return
            except Exception as exc:  # noqa: BLE001
                self._failed(exc)
        await self._fallback.set(key, value, ttl_seconds)

    async def incr(self, key: str, ttl_seconds: int | None = None) -> tuple[int, int | None]:
        if self._reachable():
            try:
                result = await self._primary.incr(key, ttl_seconds)
                self._succeeded()
                return result
            except Exception as exc:  # noqa: BLE001
                self._failed(exc)
        return await self._fallback.incr(key, ttl_seconds)

    async def close(self) -> None:
        await self._primary.close()
        await self._fallback.close()


_store: KeyValueStore | None = None


def build_store(settings: Settings) -> KeyValueStore:
    """按配置构造存储。没配 Redis 就用进程内实现。"""
    url = (settings.redis_url or "").strip()
    if not url:
        # 本地开发和 CI 走这条。不报错也不告警：没配就是明确选择了单机模式。
        return MemoryStore()
    return ResilientStore(
        RedisStore.from_url(url),
        MemoryStore(),
        cooldown_seconds=settings.cache_failure_cooldown_seconds,
    )


def get_store(settings: Settings | None = None) -> KeyValueStore:
    """取应用级存储单例。连接要复用，不能每个请求建一次。"""
    global _store
    if _store is None:
        _store = build_store(settings or get_settings())
    return _store


async def close_store() -> None:
    """关闭并丢弃当前存储，供应用退出时调用。"""
    global _store
    if _store is not None:
        await _store.close()
        _store = None


def reset_store() -> None:
    """同步丢弃存储单例（测试隔离用，不负责关闭连接）。"""
    global _store
    _store = None


class RetrievalCache:
    """检索结果缓存。

    缓存的是「命中哪些片段、各得多少分」，不是完整的 ``SearchHit``：
    后者装的是 ORM 对象，序列化进缓存既脆弱，又容易和库里的数据脱节。
    命中缓存后按 id 回库取片段，片段被删掉时会自然少返回，
    不会把已经不存在的内容继续当成依据交给模型。

    语料变更用「代次」失效而不是删 key：导入文档时把代次 +1，新 key 天然
    带新代次，旧的靠 TTL 过期。Redis 下按前缀删要 SCAN 全库，代价远高于等它过期。
    """

    def __init__(self, store: KeyValueStore, ttl_seconds: int = 300, namespace: str = "search"):
        self._store = store
        self._ttl = ttl_seconds
        self._namespace = namespace

    async def generation(self) -> int:
        value = await self._store.get(GENERATION_KEY)
        try:
            return int(value) if value else 0
        except ValueError:
            # 键被别的东西占了。当作 0 处理即可，只是缓存命中率下降。
            return 0

    async def invalidate(self) -> int:
        """语料变更后调用：代次 +1，让所有既有检索缓存一并作废。"""
        count, _ = await self._store.incr(GENERATION_KEY)
        logger.info("语料代次已更新为 %d，既有检索缓存全部作废", count)
        return count

    async def key_for(self, parts: tuple[Any, ...]) -> str:
        joined = "|".join(str(part) for part in parts)
        digest = sha256(joined.encode("utf-8")).hexdigest()[:32]
        # 用哈希而不是原文：查询可能很长且带任意字符，直接进 key 既占空间又不安全；
        # 带代次是为了让语料变更自动生效。
        return f"support_agent:{self._namespace}:{await self.generation()}:{digest}"

    async def get(self, parts: tuple[Any, ...]) -> list[tuple[str, float]] | None:
        """返回缓存的 (片段 id, 分数) 列表。

        注意 ``None`` 和 ``[]`` 的区别：前者是「没有缓存」，后者是
        「缓存了空结果」——跑题问题会被判成空命中，这个结论同样值得缓存。

        片段 id 一律按字符串处理（库里是 UUID，不是自增整数）：
        统一 str 之后，无论 id 原本是什么类型，写进去和读回来都是同一个值，
        不会出现「缓存写了但永远不命中」这种没有任何报错的静默失效。
        """
        raw = await self._store.get(await self.key_for(parts))
        if raw is None:
            return None
        try:
            return [(str(chunk_id), float(score)) for chunk_id, score in json.loads(raw)]
        except (ValueError, TypeError):
            # 缓存格式变了（比如升级后字段调整）就当作未命中，让它自然重算。
            return None

    async def put(self, parts: tuple[Any, ...], hits: list[tuple[Any, float]]) -> None:
        payload = [[str(chunk_id), score] for chunk_id, score in hits]
        await self._store.set(await self.key_for(parts), json.dumps(payload), self._ttl)


def get_retrieval_cache(settings: Settings | None = None) -> RetrievalCache:
    """构造检索缓存。包装层很轻，每次新建即可，真正需要复用连接的是 store。"""
    settings = settings or get_settings()
    return RetrievalCache(get_store(settings), ttl_seconds=settings.cache_ttl_seconds)


@dataclass(frozen=True)
class RateLimitDecision:
    """一次限流判定的结果。"""

    allowed: bool
    limit: int
    remaining: int
    retry_after: int

    @property
    def headers(self) -> dict[str, str]:
        """标准限流响应头，客户端据此决定何时重试。

        ``Retry-After`` 只在被拒时给：正常响应里带上它，客户端会以为
        自己也需要等待，反而可能主动降速。
        """
        if self.limit <= 0:
            return {}
        headers = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
        }
        if not self.allowed:
            headers["Retry-After"] = str(self.retry_after)
        return headers


class RateLimiter:
    """按主体（用户或 IP）的固定窗口限流。

    选固定窗口而不是滑动窗口：它在窗口交界处最坏会放过两倍流量，但
    「防止单个用户把模型调用打爆」这个目的用一次 INCR 就能达成，
    不必为每个请求在 Redis 里存一个时间戳再按范围清理。

    限流只挡在入口，不参与回答逻辑——被限流的请求不应该产生会话和消息。
    """

    def __init__(
        self,
        store: KeyValueStore,
        *,
        limit: int,
        window_seconds: int,
        namespace: str = "rate",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._limit = limit
        self._window = max(1, window_seconds)
        self._namespace = namespace
        # 时钟可注入：窗口边界是最容易写错的地方（差一秒就多放一轮），
        # 而靠 sleep 去验证会让测试变慢且不稳定。这里用墙钟而不是单调时钟，
        # 因为窗口编号要能被多个进程算成同一个值。
        self._clock = clock

    @property
    def enabled(self) -> bool:
        return self._limit > 0

    async def check(self, subject: str) -> RateLimitDecision:
        if not self.enabled:
            return RateLimitDecision(True, 0, 0, 0)

        now = self._clock()
        # 窗口编号进 key：跨窗时自动换一个新的计数键，不需要在代码里判断重置。
        bucket = int(now // self._window)
        key = f"support_agent:{self._namespace}:{subject}:{bucket}"
        # 过期时间给两倍窗口，保证这个键在窗口结束后一定会被清掉，
        # 不会为了「准确的 TTL」而让计数键永远堆积。
        count, _ = await self._store.incr(key, ttl_seconds=self._window * 2)

        remaining = max(0, self._limit - count)
        # 距离本窗口结束还有多久，用来告诉客户端什么时候可以重试。
        retry_after = self._window - int(now % self._window) or self._window
        return RateLimitDecision(count <= self._limit, self._limit, remaining, retry_after)


def get_rate_limiter(settings: Settings | None = None) -> RateLimiter:
    settings = settings or get_settings()
    return RateLimiter(
        get_store(settings),
        limit=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )


async def load_hits_by_id(
    db: Any,
    entries: list[tuple[str, float]],
    *,
    corpus_id: str | None,
    include_restricted: bool,
    visibility: str | None = None,
) -> list[tuple[DocumentChunk, SourceDocument, float]]:
    """按缓存的 id 回库取片段，过滤掉已不存在或已越权的记录。

    cache 键里已经带了语料和权限，这里再过滤一次是有意的双保险：
    缓存里的 id 是过去的快照，而权限判断必须永远以此刻库里的元数据为准。
    """
    if not entries:
        return []
    ids = [chunk_id for chunk_id, _ in entries]
    rows = (
        await db.execute(
            select(DocumentChunk, SourceDocument)
            .join(SourceDocument, SourceDocument.id == DocumentChunk.document_id)
            .where(DocumentChunk.id.in_(ids))
        )
    ).all()
    found: dict[str, tuple[DocumentChunk, SourceDocument]] = {}
    for chunk, document in rows:
        metadata = chunk.chunk_metadata or {}
        if corpus_id and metadata.get("corpus_id") != corpus_id:
            continue
        if not include_restricted and metadata.get("restricted") is True:
            continue
        is_public = metadata.get("visibility") == "public"
        if (visibility == "public" and not is_public) or (
            visibility == "private" and is_public
        ):
            continue
        found[str(chunk.id)] = (chunk, document)

    ordered: list[tuple[DocumentChunk, SourceDocument, float]] = []
    for chunk_id, score in entries:
        pair = found.get(str(chunk_id))
        if pair is not None:
            ordered.append((pair[0], pair[1], score))
    return ordered
