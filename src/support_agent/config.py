from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# 各向量后端的默认维度。放在配置层是为了让「维度」只有一处定义：
# models.py 建表、semantic.py 加载模型都读这里，不会各写一个数。
EMBEDDING_DIMENSIONS = {"hash": 384, "onnx": 512}


class Settings(BaseSettings):
    """集中读取应用配置；同名环境变量会覆盖这里的默认值。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    auth_enabled: bool = True
    database_url: str = "sqlite+aiosqlite:///./support_agent.db"
    # 检索缓存与限流共用的存储。留空则用进程内实现：本地开发和 CI 走这条，
    # 功能完整，只是多进程下缓存命中率下降、限流额度按进程数放大。
    redis_url: str | None = None
    # 检索结果的缓存有效期。设短一点，语料更新后最多滞后这么久
    # （另有语料代次做主动失效，这个 TTL 是兜底）。
    cache_ttl_seconds: int = 300
    # 存储不可用后的冷却秒数。冷却期内不再尝试连接，直接走进程内实现，
    # 避免每个请求都吃一遍连接超时。
    cache_failure_cooldown_seconds: int = 30
    # 单个用户在每个窗口内允许的 /chat 次数，0 表示不限流。
    rate_limit_requests: int = 30
    rate_limit_window_seconds: int = 60
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    # 公司资料默认只在本机处理；配置了密钥也不会自动启用外部模型。
    allow_remote_llm: bool = False
    # 双资料库分流需显式开启；远程模型能否处理内部资料由当前业务规则决定。
    privacy_routing_enabled: bool = False
    local_llm_base_url: str | None = None
    local_llm_model: str | None = None
    local_llm_api_key: str = "local"
    public_search_api_key: str | None = None
    # 向量后端：hash 是零依赖兜底，onnx 是本地语义模型（需要模型目录）。
    embedding_backend: str = "hash"
    embedding_model_path: str | None = None
    # 留空则按后端取 EMBEDDING_DIMENSIONS 里的默认值。
    embedding_dimension: int | None = None
    embedding_batch_size: int = 16
    embedding_max_length: int = 512
    confirmation_secret: str = "development-only-secret"
    max_upload_bytes: int = 5 * 1024 * 1024
    retrieval_top_k: int = 5
    retrieval_corpus_id: str | None = None
    retrieval_min_score: float = 0.0
    chunk_size: int = 700
    chunk_overlap: int = 100
    # 单个请求最多允许模型来回几轮；轮数用尽后服务端会禁用工具强制收敛。
    agent_max_rounds: int = 5
    # 注入给模型的历史消息条数上限，避免长会话把提示词越堆越大。
    chat_history_limit: int = 10

    @property
    def llm_enabled(self) -> bool:
        """显式允许外发且远程模型配置齐全时才启用模型调用。"""
        return bool(
            self.allow_remote_llm and self.llm_base_url and self.llm_api_key and self.llm_model
        )

    @property
    def local_llm_enabled(self) -> bool:
        from urllib.parse import urlsplit

        url = urlsplit(self.local_llm_base_url or "")
        return bool(
            self.local_llm_model
            and url.scheme == "http"
            and url.hostname in {"127.0.0.1", "localhost", "::1"}
        )

    @property
    def vector_dimension(self) -> int:
        """当前向量后端使用的维度。

        换后端会让新旧向量不可比，所以这个值同时决定了 ``document_chunks.embedding``
        的列宽——改配置后必须重建表并重新导入语料。
        """
        if self.embedding_dimension:
            return self.embedding_dimension
        return EMBEDDING_DIMENSIONS.get((self.embedding_backend or "hash").lower(), 384)


@lru_cache
def get_settings() -> Settings:
    return Settings()
