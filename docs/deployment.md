# 部署说明

[README](../README.md) 讲的是「怎么在开发机上跑起来」，这份讲「怎么把它放到一台服务器上、怎么升级、怎么回滚」。
内容全部对应仓库里实际存在的文件与命令，没有设想中的组件。

## 三种运行形态

| 形态 | 数据 | 缓存与限流 | 适用 |
|---|---|---|---|
| 本地演示（默认） | SQLite 文件 `support_agent.db` | 进程内实现 | 开发、演示、离线试用。零外部依赖 |
| 单机生产 | PostgreSQL | Redis（**多 worker 时必须**） | 一台机器上的小规模部署 |
| 容器编排 | PostgreSQL 容器（命名卷） | Redis 容器 | `docker compose up -d`，一条命令起全套 |

三种形态跑的是同一份代码，差异只在环境变量。判断当前落在哪一种，看 `/health` 就够了。

## 安装

从 wheel 安装（`dist/` 里有构建产物，也可以从 CI 的 artifact 取）：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install smartpv_support_agent-1.10.0-py3-none-any.whl
```

装完先跑自检，把它当成部署门禁：

```powershell
smartpv-agent doctor --strict
```

`--strict` 会让**警告也算失败**。不加这个开关时只有 `FAIL` 才返回 1，因为「没配远程模型」是受支持的默认模式
（退回本地规则模型），新克隆的仓库和 CI 都长这样。生产环境要求「一切配置就位」，所以用 `--strict`。

## 启动

```powershell
smartpv-agent serve --host 0.0.0.0 --port 8000
# 等价：uvicorn support_agent.main:app --host 0.0.0.0 --port 8000 --workers 4
```

**加 `--workers` 之前，先确认这三件事：**

| 事项 | 不加处理的后果 |
|---|---|
| 配了 `REDIS_URL` | 限流按进程计数，4 个 worker 等于额度放大 4 倍；缓存命中率按进程分摊 |
| 用 PostgreSQL | SQLite 在多进程写入时会锁库 |
| `CONFIRMATION_SECRET` 是随机值 | 令牌可伪造（见下节） |

## 配置注入

配置从环境变量读，也可以放 `.env`（`config.py` 的 `env_file`）。生产建议用环境变量或密钥管理服务注入，
不要把 `.env` 复制到服务器上——它已经在 `.gitignore` 里，说明它不该离开开发机。

| 变量 | 生产取值 | 说明 |
|---|---|---|
| `CONFIRMATION_SECRET` | 随机值，≥32 字节 | **必改**。默认值 `development-only-secret` 只适合本地，泄漏等于令牌可伪造 |
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@host:5432/db` | 留空则用 SQLite |
| `REDIS_URL` | `redis://host:6379/0` | 多 worker 必配 |
| `APP_ENV` | `production` | 只影响 `/health` 的显示，不改变行为 |
| `PRIVACY_ROUTING_ENABLED` | `true` | 普通/隐私资料分流；生产部署必须显式开启 |
| `ALLOW_REMOTE_LLM` | 默认 `false` | 仅普通资料获准外发后设为 `true` |
| `LOCAL_LLM_BASE_URL` / `LOCAL_LLM_MODEL` | 本机回环地址与模型名 | 隐私问题需要本地生成时配置 |
| `PUBLIC_SEARCH_API_KEY` | 可留空 | 隐私问题本地无依据时，用安全短查询查公开网页 |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | 三项**同时**非空 | 缺一项就退回本地规则模型 |
| `EMBEDDING_BACKEND` / `EMBEDDING_MODEL_PATH` / `EMBEDDING_DIMENSION` | **与建库时一致** | 换后端必须重建索引，否则向量空间对不上 |
| `RETRIEVAL_CORPUS_ID` | 与导入时一致 | 留空表示检索全部语料 |
| `RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS` | 按容量定 | 0 表示不限流 |
| `MAX_UPLOAD_BYTES` | 与反向代理的 `client_max_body_size` 对齐 | 默认 5MB |

生成密钥：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## 数据库

### SQLite 形态

整个状态就是一个文件。备份 = 停服务后拷 `support_agent.db`。没有并发写入需求时这是最省事的形态。

### PostgreSQL 形态

`scripts/init.sql` 会建 `vector` 扩展（compose 通过 `docker-entrypoint-initdb.d` 挂载，只在**首次初始化数据卷**时执行）。

表结构演进要单独说，因为这里有一个真实的坑：

**这个项目没有引入 Alembic。** 建表靠 `Base.metadata.create_all`，而它的行为是**只补建缺失的表，不改已存在表的列名**。
于是改过一次列名（`tickets.order_id` → `device_sn`）之后，SQLite 那边重建过库所以没事，
PostgreSQL 的命名卷却一直停在旧结构——直到某次创建工单才炸出 500：

```
asyncpg.exceptions.UndefinedColumnError: column "device_sn" of relation "tickets" does not exist
```

所以**部署前后各跑一次结构检查**，有漂移就中止发布：

```powershell
python scripts/check_schema.py              # 逐表对比代码定义与库里实际列，有漂移退出 1

# 容器内
docker compose exec -T api python - < scripts/check_schema.py
```

脚本会列出差异并给出候选的改名语句。该表为空时修起来最安全：

```sql
ALTER TABLE tickets RENAME COLUMN order_id TO device_sn;
```

> 如果将来真的要频繁演进表结构，正确的方向是引入迁移工具（Alembic）而不是继续手工 `ALTER`。
> 当前规模下手工处理 + 自动检查是刻意选的轻量方案。

## 语料

语料既不进仓库也不进镜像（它随资料更新而变，也可能含内部材料）。容器起步时知识库是空的，要显式导入：

```powershell
# 整批分卷（需要目录里有 index.json 与 知识库分卷/）
python scripts/ingest_smartpv.py --source "<资料目录>" --reset

# 零散单份（docx / md / txt）
python scripts/ingest_document.py --file "<文件>" --dry-run   # 先预览章节切分
```

容器模式是从宿主机把语料导进映射出来的端口：

```powershell
$env:DATABASE_URL="postgresql+asyncpg://agent:agent@127.0.0.1:5433/agent"
python scripts/ingest_smartpv.py --source "<资料目录>" --reset
```

**这四种情况下必须重建索引**（`--reset`）：

| 情况 | 原因 |
|---|---|
| 换了 `EMBEDDING_BACKEND` 或维度 | 向量空间不同，新旧向量不可比 |
| 改了切块参数 `CHUNK_SIZE` / `CHUNK_OVERLAP` | 切块只影响新导入的文档，旧文档按校验和整份跳过 |
| 改了行业标签抽取规则 | 导入按校验和去重，内容没变就不会更新元数据 |
| 改了列名或表结构 | 先 `check_schema.py`，再重建 |

导入完成后**清一次缓存**：宿主机导入不会通知正在运行的服务，语料代次没变，300 秒 TTL 内仍会读到旧结果。

```powershell
docker compose exec redis redis-cli FLUSHALL
```

## 反向代理

`/chat` 一次请求可能触发多轮模型往返，耗时远超普通接口。Nginx 示例：

```nginx
server {
    listen 443 ssl;
    client_max_body_size 5m;          # 与 MAX_UPLOAD_BYTES 对齐

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 120s;      # 默认 60s 会在长回答上截断
        proxy_send_timeout 120s;
    }
}
```

**首次部署需建立管理员账号。** 在应用工作目录运行 `python scripts/create_admin.py --generate`，保存只显示一次的密码。
页面登录后服务端签发 HttpOnly 会话 Cookie；聊天、会话、导入和评测接口均按服务器验证的身份授权。
admin 可在页面的「管理用户」创建、停用和重置 user 账号；user 只能使用问答、刷题、反馈、工单和自己的会话。
生产环境仍需使用 HTTPS，并设置随机的 `CONFIRMATION_SECRET`。见 [SECURITY.md](../SECURITY.md)。

## 健康检查

```powershell
curl -f http://127.0.0.1:8000/health
```

返回的每个字段都对应一类故障：

| 字段 | 看什么 |
|---|---|
| `version` | 升级后确认跑的确实是新版本，别靠记忆 |
| `embedding_backend` / `embedding_dimension` | 与建库时不一致 → 检索结果不可解释，需要重建索引 |
| `cache.degraded` | `true` 表示 Redis 不可达、已熔断降级。**接口仍可用**，但命中率与限流额度都会打折 |
| `llm_enabled` | `false` 表示在跑本地规则模型，回答质量与线上不同 |
| `rate_limit` | 当前生效的额度 |

容器与 compose 都已内置 healthcheck；K8s 里可以直接用它当 liveness 探针。

## 上线检查清单

- [ ] `CONFIRMATION_SECRET` 已换成随机值，不是默认的两个占位值
- [ ] `.env` 没有进仓库、也没有打进镜像
- [ ] `smartpv-agent doctor --strict` 全绿
- [ ] `scripts/check_schema.py` 两侧都没有漂移
- [ ] `/health` 的 `embedding_backend` / `embedding_dimension` 与建库时一致
- [ ] 语料已导入，`doctor` 报出的文档与片段数符合预期
- [ ] 已创建 admin 账号，能登录、修改密码，未登录业务请求返回 401
- [ ] 代理超时 ≥ 120s，`client_max_body_size` 与 `MAX_UPLOAD_BYTES` 对齐
- [ ] 跑通一次冒烟：知识问答返回 `completed` / `knowledge`，写操作返回 `pending_confirmation`

## 升级与回滚

升级：

1. 备份数据库（SQLite 拷文件；PostgreSQL 用 `pg_dump`）
2. 装新版本：`pip install --force-reinstall <新 wheel>`，或 `docker compose up --build -d`
3. `python scripts/check_schema.py` 确认结构一致
4. `curl /health` 核对 `version` 与向量后端
5. 冒烟一条知识问答

回滚：

1. 装回旧版本 wheel（版本号从升级前的 `/health` 输出里取，不要凭记忆）
2. 升级期间若做过 `ALTER`，要反向 `ALTER` 或还原备份
3. Redis 不需要处理，`FLUSHALL` 即可——缓存按设计就是可丢的，服务不依赖它
