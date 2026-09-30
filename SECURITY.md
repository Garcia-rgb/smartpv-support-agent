# 安全策略

## 这个项目是什么、不是什么

一个面向内部技术支持场景的光伏电站问答与办事服务。当前知识库可能含真实工作记录；问答接入远程模型时，命中的资料片段会外发。
下面的边界是照着代码逐条写的，写进来的都在实现里指得到位置。

## 已经实现的边界

| 边界 | 实现位置 | 行为 |
|---|---|---|
| 提示词注入拦截 | `services/security.py` | 在 Agent Loop **之前**判定，命中返回 `blocked`，模型一次都不调用 |
| 工具白名单 | `services/agent_loop.py` 的 `build_tool_registry()` | 进程内注册表是唯一可信执行表；模型只能按名字申请，未注册的名字直接拒绝 |
| 参数校验 | 各 `ToolSpec.parameters` + 服务端校验 | `additionalProperties: False`，未知参数**拒绝**而不是忽略 |
| 工具说明书与执行函数分离 | `ToolSpec.as_schema()` | 发给模型的结构里只有名字、说明和参数；`handler` 永不出现 |
| 写操作不直接执行 | `services/agent.py` + `main.py` | 模型提出写操作时被拦下，改回一次性确认令牌，由人点击确认后才落库 |
| 确认令牌四道闸 | `services/security.py`、`main.py` 的 `POST /tickets` | HMAC 签名 → 有效期 → 用户归属 → 单次使用，依次通过才建单 |
| 防重放 | `consumed_confirmation_tokens` 表 | `token_hash` 是主键，重复写入撞唯一约束返回 409；判定交给数据库而不是「先查再写」 |
| 令牌不落原文 | `main.py` | 只存 `sha256(token)`——令牌本身是凭证，落库等于多留一份可用的口令 |
| 按用户限流 | `services/cache.py`、`main.py` | 判定放在 `/chat` 最前，被拒请求不建会话、不写消息、不调模型 |
| 上传限制 | `main.py` 的 `POST /documents` | 大小上限（`MAX_UPLOAD_BYTES`）+ 类型白名单 + SHA-256 去重 |
| 登录身份 | `services/auth.py`、`/auth/*` | 密码哈希、数据库会话、HttpOnly Cookie、退出和改密；不信任浏览器自报的用户编号 |
| 管理员权限 | `POST /documents`、`POST /evaluations/run` | 仅 admin 可以导入资料和运行评测 |
| 会话所有权 | `GET /sessions/{id}`、`GET /sessions` | 依照服务端登录身份过滤；admin 可查看历史会话 |
| 审计 | `audit_logs` 表 | 写操作与令牌消费都留痕（`actor` / `action` / `resource` / `detail`） |
| 密钥不外泄 | `config.py`、`cli.py` | 只从环境变量与 `.env` 读；`.env` 已 gitignore；`doctor` 只报「已配置」不打印内容 |

## 部署前必须做的三件事

1. **换掉 `CONFIRMATION_SECRET`**。代码默认值（`development-only-secret`）与 compose 的
   `local-compose-secret` 都只适合本地。这个密钥泄漏等于令牌可以伪造。
2. **不要提交 `.env`**。`LLM_API_KEY` 与 `CONFIRMATION_SECRET` 都从环境变量注入。
3. **跑在 TLS 终止后面**，创建 admin 账号并确认 `AUTH_ENABLED=true`。

## 已知边界：不要误解这几条

- **目前只有初始管理员账号的创建流程。** 普通 user 账号管理和企业统一登录尚未接入。
- **本地账号仅在 HTTPS 下可供远程人员使用。** 开发环境的本机 HTTP 仅供本机演示。
- **限流是固定窗口、按用户计数。** 不配 `REDIS_URL` 时按进程计数，多 worker 部署下实际额度会按进程数放大。
- **Redis 未设密码。** compose 里它只在容器网络内可达（没有把端口映射到宿主机）。换到共享网络请自行加认证。
- **上传只做表层校验**：扩展名、大小与校验和，不做内容深度检查。别把这份服务直接暴露成公开的文档入口。
- **语料与评测集不进仓库。** 本机资料库可能包含真实工作记录；不要把数据库或资料目录提交到仓库。

## 上报问题

在 GitHub 仓库开 issue：<https://github.com/Garcia-rgb/smartpv-support-agent/issues>。
请不要在 issue 里贴出真实密钥、令牌或客户数据——贴日志前先把这两样改掉。
