"""光伏电站技术支持 Agent 应用包。

版本号只在 ``__version__`` 维护一处：构建配置（``pyproject.toml``）由 hatch 从它动态读取，
``/health``、OpenAPI 文档与命令行入口（``smartpv-agent version``）引用的是同一个值，
不会出现「文档写着 1.0.0、运行时还是 0.1.0」这种分叉。

版本遵循语义化版本（SemVer）：主版本号变更表示对外的接口或响应协议有不兼容改动，
次版本号表示新增能力，修订号表示向后兼容的修复。
"""

__version__ = "1.9.0"

# 便于代码里做版本比较，例如「片段是本版本之前的写法，需要重导语料」。
__version_info__ = tuple(int(part) for part in __version__.split("."))

__all__ = ["__version__", "__version_info__"]
