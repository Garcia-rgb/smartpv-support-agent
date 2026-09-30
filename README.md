# 光伏电站技术支持 Agent

[![version](https://img.shields.io/badge/version-1.9.0-blue)](CHANGELOG.md)
[![python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-3776ab)](pyproject.toml)
[![coverage](https://img.shields.io/badge/coverage-88.84%25-brightgreen)](pyproject.toml)
[![license](https://img.shields.io/badge/license-MIT-yellow)](LICENSE)
[![code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000)](https://github.com/astral-sh/ruff)

当前版本 **1.9.0**，包名 `smartpv-support-agent`，命令行入口 `smartpv-agent`。版本变更见 [CHANGELOG.md](CHANGELOG.md)。

这是一个面向光伏电站技术支持场景的问答与办事服务：工程师把现场现象或问题丢进来，它去知识库里找依据、
必要时查设备档案或算一段，涉及写操作（建工单）时先要人工确认。项目覆盖 FastAPI、数据库、RAG、LangGraph、
工具调用、人工确认、安全审计、离线评测、测试和 Docker。

默认模式不需要模型 API、PostgreSQL 或 Redis：SQLite 保存数据，本地规则模型展示检索结果。当前启用分库后，普通与内部问题都可由 DeepSeek 整理命中资料。图片答题需安装 `quiz` 可选依赖。

## 资料隐私分流

设置 `PRIVACY_ROUTING_ENABLED=true` 后，新导入资料默认属于隐私库。普通问题只检索普通库，内部问题可检索两类资料。按当前用户授权，两类问题都会把本次问题及命中片段交给 DeepSeek 整理，不附带聊天历史；内部资料会离开本机。资料不足时，仅用白名单技术词尝试公开搜索。具体范围见 [资料分流说明](docs/privacy_routing.md)。

## 管理员登录

首次启动前运行 `python scripts/create_admin.py --generate`，记下只显示一次的初始密码，再打开网页以 `admin` 登录。admin 可通过右上角「管理用户」创建 user 账号、停用账号和重置密码；初始密码只显示一次，需交给对应人员。user 登录后可问答、刷题、反馈、建自己的工单并看自己的会话，不能导入资料、运行评测或管理账号。两类账号都能在右上角修改自己的密码。服务端默认启用身份验证，浏览器自报的 `user_id` 不再决定实际身份。已有 `demo-user` 会话保留，管理员可在历史会话中查看。

## 统一文字与图片输入

在输入框写文字，或点击「📷 上传图片」上传 PNG/JPG/WebP 截图，也可以直接粘贴截图。图片先在本机识别文字；有明确题目结构时走刷题讲解，客户消息、告警或现场问题截图走普通问答。识别文字可以展开核对和修改。扫描照片中没有可识别文字时会提示重新提供清晰截图或手动输入；当前版本不理解纯图像中的设备外观和故障现象。资料不能明确支持某个选项时会提示核对，不会猜测。

```powershell
pip install -e ".[quiz]"
```

## 点表制作

登录后点「点表制作」，先选南向或北向，再上传设备协议 Word（.doc/.docx）或文字版 PDF。服务在本机抽取带地址的候选点位，供逐点修改和增删；南向需填写现场设备信息，北向需填写平台点号，然后下载 CSV。协议不发送给 DeepSeek，生成的 CSV 不写入服务端数据库。扫描版 PDF 须先做 OCR；不同协议版式可能需要人工补点。地址基数、字序、倍率、控制值及现场导入结果仍须核对。

配了 `REDIS_URL` 时，检索缓存与按用户限流走 Redis；没配就用进程内实现，配了但连不上会熔断降级到同一套进程内实现——两种情况接口都照常工作，只是多进程下降级期间缓存命中率会下降、限流额度按进程数放大。`/health` 的 `cache.degraded` 会告诉你当前走的是哪条路。

## 安装

按包安装（推荐，装完就有 `smartpv-agent` 命令）：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"          # 开发模式；只跑服务用 pip install .
smartpv-agent doctor             # 逐项自检：配置 / 数据库 / 语料 / 向量后端 / 模型 / 缓存
smartpv-agent serve              # 等价于 uvicorn support_agent.main:app
```

需要本地语义向量时再加一个 extra（含 onnxruntime 与 tokenizers，约 200MB）：

```powershell
pip install -e ".[semantic,dev]"
```

也可以构建成可分发的 wheel：

```powershell
python -m build                  # 产物在 dist/，可直接 pip install 到别的环境
```

## 快速开始（Windows PowerShell，Conda 路线）

```powershell
git clone https://github.com/Garcia-rgb/smartpv-support-agent.git
cd smartpv-support-agent
conda env create -f environment.yml
conda activate smartpv-support-agent
Copy-Item .env.example .env
python scripts/ingest_smartpv.py --source "D:\资料汇总"
uvicorn support_agent.main:app --reload
```

环境已存在时，用下面的命令同步依赖：

```powershell
conda env update -f environment.yml --prune
conda activate smartpv-support-agent
```

打开 <http://127.0.0.1:8000/docs> 使用 Swagger。运行测试：

```powershell
pytest -q
ruff check .
```

完整基础设施模式（PostgreSQL + Redis）：

```powershell
docker compose up --build -d          # 首次构建镜像约 3 分钟
curl http://127.0.0.1:8000/health     # cache.backend 应为 redis、degraded 应为 false
docker compose down                   # 停服务；加 -v 才会连数据卷一起删
```

三个容器各司其职：`api`（8000）、`postgres`（`pgvector/pgvector:pg16`，映射到宿主机 5433）、`redis`（只在集群内部）。
API 容器的 `/health` 会多出 `cache.backend` / `cache.primary` / `cache.degraded` 三个字段，这是确认 Redis
真的被用上、而不是悄悄降级到进程内实现的第一站。容器内默认用哈希向量（384 维），因为镜像不装 onnxruntime。

容器里的知识库初始是空的，语料要从宿主机导入——把 `DATABASE_URL` 指向映射出来的 5433 端口就行：

```powershell
$env:DATABASE_URL="postgresql+asyncpg://agent:agent@127.0.0.1:5433/agent"
$env:EMBEDDING_BACKEND="hash"
$env:EMBEDDING_DIMENSION="384"
python scripts/ingest_smartpv.py --source "D:\资料汇总" --reset
python scripts/ingest_document.py --file "D:\资料汇总\拓展资料\第3册-交直流屏柜原理.md"
```

两个和 PostgreSQL 有关的坑，排障时先看这里：

- **改列名后表结构不会自动跟上**。`create_all` 只补建缺失的表，不改已存在表的列名。SQLite 那边重建过库就没事，
  而 PG 走的是命名卷 `postgres_data`，会一直停在旧结构，直到某次写入才炸出 500（`column "device_sn" ... does not exist`）。
  查漂移用 `python scripts/check_schema.py`；容器内用
  `docker compose exec -T api python - < scripts/check_schema.py`。有漂移时退出码为 1，可以串进部署前检查。
- **Redis 没有数据卷**，`docker compose down` 之后缓存为空是预期行为——缓存本身就是可丢的，接口不依赖它，
  限流计数会从零开始。
- **`Dockerfile` 里 `COPY static` 必须排在 `pip install` 之前**。`pyproject.toml` 用 hatch 的 `force-include`
  把 `static/` 打进 wheel，构建元数据阶段找不到这个目录会直接让 `pip install` 失败
  （`FileNotFoundError: Forced include not found: /app/static`）。

## 命令行

```powershell
smartpv-agent version          # 版本号、Python、平台与关键依赖版本
smartpv-agent doctor           # 环境自检，带退出码，可直接串进 CI 或部署脚本
smartpv-agent doctor --strict  # 把警告也算作失败（部署门禁用，要求全绿）
smartpv-agent serve --host 0.0.0.0 --port 8000 [--reload]
```

`doctor` 的输出长这样（下面是没配远程模型时的样子，`model` 那行是 WARN，其余全 OK）：

```text
SmartPV Support Agent 1.4.0 | doctor

[OK  ] configuration  app_env=development
[OK  ] database       sqlite+aiosqlite:///./support_agent.db | 20 documents / 212 chunks
[OK  ] embedding      onnx | dimension=512 | model=D:\...\bge-small-zh-v1.5 | files present (use --deep to load)
[WARN] model          not configured (LLM_BASE_URL / LLM_MODEL / LLM_API_KEY) -> /chat uses the local rule-based model
[OK  ] retrieval      top_k=5 | min_score=0.0 | corpus_id=(all) | refusal gate: phrase-missing >= 0.6 once corpus has >= 60 chunks
[OK  ] cache          {"backend": "memory"}
[OK  ] rate limit     30 requests / 60s per user (0 disables it)

result: 1 warning(s), no failures
```

`cache` 一行只在配了 `REDIS_URL` 时才会多出 `primary` 与 `degraded` 两个字段（进程内实现是默认路径，没有「主备」可言）。

三处设计上的取舍值得说明：`embedding` 默认只查依赖与模型文件是否存在，加 `--deep` 才会真的加载模型编一句话
（加载 90MB 模型只为了报一行状态并不划算）；`cache` 会先真实读写一次再看状态——配了 `REDIS_URL` 却连不上时，
熔断要先被触发过才会打开，不主动探测就会显示成一切正常。

**退出码把警告和失败分开。** 默认只有出现 `FAIL` 才返回非零：没配远程模型是**受支持的默认模式**
（退回本地规则模型），新克隆的仓库和 CI 都长这样，若把警告也算失败，这条命令就没法像上面说的那样串进 CI，
每次都会红。需要「全绿才算过」的部署门禁加 `--strict`，它会把警告一并升级为失败。

## 这个版本改了什么

四个阶段的改动各自解决一个具体问题。下表按「行为改变 → 实测量」列出，数字在 [CHANGELOG.md](CHANGELOG.md) 与
[docs/architecture.md](docs/architecture.md) 里能对回原始记录：

| 改动 | 改之前的行为 | 改之后的行为 | 实测 |
|---|---|---|---|
| 查询侧剥虚词 + 二元字组打分（0.2.0） | 「啥是合母」这类口语问法排不进前 5，「合母和控母有什么区别」排第 9、「这两个母线有啥区别」排第 23 | 逐字切分退化成共享「母」字的问题解决，8 个口语问法全部进前 5 | 三路打分 `0.30 单字 + 0.35 二元字组 + 0.35 向量` |
| 语料范围判据 + 前置判定（0.3.0；1.1.0 改成追问） | 库外问题返回 `completed` / `model` / 0 引用——话术是模型自己写的，服务端兜底永远等不到 | 判定移到调模型之前：命中即 `needs_clarification` / `policy`，请用户补型号或现象，模型零调用 | 30 条库内全部放行、30 条库外全部拦下；10 条口语化真问题从「答不出」变成「多问一句」 |
| 本地 ONNX 语义向量（0.4.0） | 哈希向量只做字符碰撞，把「Python 怎么装环境」与语料里的「安装环境」判成相似 | 512 维 `bge-small-zh-v1.5` 本地推理，`[CLS]` 池化 | 库内 top1 最低分 0.226 → 0.324，术语命中率 top-1 83% → 87% |
| 行业元数据加权（0.2.0） | 户用、工商业、地面电站的资料用词高度重合，排序不分场景 | 命中机型/协议/场景 +0.06、明确属于别类 −0.10，只影响排序不硬过滤 | 「户用逆变器怎么组网」前 5 条从混入地面电站收敛到户用的 2.2 / 2.6 组网章节 |
| 跨版本并列提示（0.2.0） | 依据落在两个资料版本时，模型自己挑一个讲 | 15% 分数差内的不同版本并列返回，由用户按现场型号裁决 | 真模型实测「直流母线电压是多少」触发 `conflicts=2`（V2.0 + 第3册） |
| Redis 检索缓存 + 熔断（1.0.0） | 每次 `/chat` 都重算一遍全语料词频 | 命中缓存直接返回片段与分数；存储不可用时熔断降级，接口不受影响 | 库内 385ms → 11ms，跑题 340ms → 0.3ms；Redis 不可达时首次 1008ms、之后 0.0ms |
| 按用户限流（1.0.0） | 单个用户可以无上限触发模型调用 | 固定窗口计数，判定放在 `/chat` 最前，被拒请求不建会话、不写消息、不调模型 | 限额 3 时第 4 次 429 只用 0.006s，且会话数是 3 而不是 4 |
| 工具类问题豁免判据（1.1.1） | 带序列号或工单诉求的问法被判成跑题，设备查询与建单永远走不到：`SN-2024-000123 这台设备现在什么状态` 缺失比例 0.62、`帮我建个工单` 0.67 | 带结构化锚点（序列号 / 算式 / 工单词）的问题不参与语料范围判定，交给模型去选工具；判据本身没动 | 设备查询回到 `completed` / `tool`（返回机型、功率、固件），建单回到 `pending_confirmation` 并带令牌；对照组跑题问题仍被拦、模型零调用 |
| 回答去掉标签噪声（1.1.3） | 片段标签罗列机型清单（最长 224 字），提示词两处又都要求「回答时带上这些标签」，模型便照抄进正文——一条排查回答 1814 字里 581 字是标签 | 机型超过 3 个归纳成「N 个机型通用」；提示词改为用一句短标签带过、不复述标签、不交代检索过程，免责提醒只在真有版本差异时出现 | 同一问题 1814 字 → 940 字且无标签罗列，改为 7 步可执行排查并附算例；`conflicts` 仍并列两版，适用范围差异仍点明 |
| 工具容错（1.2.0） | 同步工具直接调用会占住事件循环，`wait_for` 的超时形同虚设；模型用同样的参数反复重试，一次提问被放大成几十次调用；失败只有一串中文，统计只能靠关键词匹配 | 同步 handler 先丢进线程再等；同一个「工具 + 参数」连失 2 次后服务端不再放行；失败按 `error_kind` 分七类 | `time.sleep(0.3)` 的工具在 0.05 秒超时下返回 `timeout`；重复调用第 3 次返回 `repeated_failure`，handler 只真正跑了 2 次 |
| MCP Server（1.2.0） | 设备能力只能在进程内被调用，外部 Host 接不进来 | 暴露 Tool `query_device`、Resource `device://catalog`、Prompt `fault_report`，跨进程走 stdio JSON-RPC；只提供只读能力，不暴露任何写操作 | 跨进程握手到 `smartpv-device`，三类都能列出并调用；查不存在的设备返回 `isError=True` 且文本可读（第一版写成普通返回值时是 `isError=False`，协议层看不出调用失败） |
| 防重放改成写入即判定（1.2.1） | 在 `audit_logs` 里「先查再写」判断令牌用过没有，检查与写入之间有时间窗，并发下两个请求都能查到「没人用过」，同一张令牌建出两条工单 | 新增 `consumed_confirmation_tokens` 表，`token_hash` 做主键，重复写入直接撞唯一约束；消费记录与工单同一事务提交 | 同一张令牌第二次提交返回 409；并发的判定交给数据库唯一约束，不再依赖两次查询之间的时间差 |
| 验签拒绝原因分开（1.2.1） | 签名比对写在 `try` 内部，抛出的 `ValueError` 被自己的 `except` 接走、重包成笼统的「确认令牌无效」，导致「签名无效」是永远走不到的死代码；原用例 `match="无效"` 的松匹配把它掩盖了过去 | 签名比对移出 `try`，四种结果分得开：格式无效 / 签名无效 / 已过期 / 内容无效 | 改动正文的伪造令牌返回「确认令牌签名无效」，过期令牌返回「确认令牌已过期」，`python examples/confirmation_token_demo.py` 四段可复现 |
| 切块参数对照实验（1.3.0） | `700/100` 是拍的，从没跑过对照；更细的切块一直有人推荐 | 三组参数各建一份独立索引，同一份语料与评测集只换切块粒度，量 `hit@1` / `hit@3` / `hit@5` / `MRR` | 300/50 是 446 片段 / `hit@1` 0.625，700/100 是 207 片段 / 0.700，1200/150 是 143 片段 / 0.700。细切块片段数翻倍且更差；粗切块与默认值差在 1–2 条样本内、方向不一致，故维持 700/100。三组空结果完全是同一批 7 条，说明语料判据与切块粒度无关 |
| 导入脚本读切块配置（1.3.0） | `scripts/ingest_smartpv.py` 直接用 `RAGService` 的构造默认值（700/100）当参数，`.env` 里改 `CHUNK_SIZE` 只会影响别处，导入仍按 700 切 | 改为读 `settings.chunk_size / chunk_overlap`，并开 `--chunk-size` / `--overlap` 供对照实验逐组覆盖 | 命令行加参数后三组索引的片段数为 446 / 207 / 143，与 `chunk_pages` 的预期一致 |

两条被实测否掉、因此刻意没做的路线，也记在这里，免得后来者（包括我自己）再试一遍：

- **用分数阈值拒答**。哈希向量下库内最低 0.182、库外最高 0.353；换真语义向量后库外最高反而涨到 0.486。
  词面与语义两个维度都试不出干净的分界线，所以拒答改用「问题是否属于这份语料」的词汇判据，`RETRIEVAL_MIN_SCORE` 保持 0.0。
- **用 pgvector 做向量近邻检索**。打分用的 IDF 权重依赖全语料词频，只取近邻候选算不出这份 df；
  212 个片段全量扫的代价可以忽略。真要做，顺序是先把词频统计落库，再加 ANN 预筛与索引。

## 本地客户端

不想每次敲命令时，用桌面上的 **`光伏电站技术支持 Agent.cmd`**：双击即启动服务并自动打开浏览器。
它由下面的脚本生成，重复执行会覆盖：

```powershell
python scripts/create_desktop_launcher.py
```

命令行等价写法是 `python scripts/start_client.py`。启动时打印知识库规模、模型模式和实际访问地址；
配置了本机模型时会尝试启动已安装的 Ollama，关闭客户端时结束由它启动的服务。
8000 端口被占用时（例如 Docker Desktop 会占用它）自动顺延到 8010、8020…，远程模型直连不通时自动尝试本机代理。

页面本体是 `static/index.html`，一个不依赖构建工具的页面：左侧会话列表，回答下方是可展开的引用来源，
写操作渲染成需要点击确认的工单卡片，另有回答反馈和文档导入。它只调用已有接口，自身不含业务逻辑。

## 模型配置

配置文件是仓库根目录的 **`.env`**（已在 `.gitignore` 中，不要提交真实密钥）。
远程模型默认关闭。按当前授权使用两类资料时，须同时设置 `PRIVACY_ROUTING_ENABLED=true`、`ALLOW_REMOTE_LLM=true` 与下面三项配置；未启用远程模型时，`/chat` 使用本地检索摘要：

```dotenv
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
LLM_API_KEY=sk-你的密钥
PRIVACY_ROUTING_ENABLED=true
ALLOW_REMOTE_LLM=true
```

常见服务的取值：

| 服务 | `LLM_BASE_URL` | `LLM_MODEL` |
|---|---|---|
| DeepSeek | `https://api.deepseek.com` | `deepseek-v4-flash` / `deepseek-v4-pro` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| 任意兼容网关 | 网关根地址（代码会自动补 `/chat/completions`） | 网关给出的模型名 |

填完后用自检脚本确认链路通了（会真实调用一次，不写数据库、不产生写操作）：

```powershell
python scripts/check_llm.py
```

需要说明的一点：DeepSeek V4 的思考模式**默认开启**，而携带 `tools` 的请求必须把历史轮次的
`reasoning_content` 原样回传，否则续轮会被拒。适配层已经处理这件事；换成其他思考模式模型时，
如果遇到第二轮 400，先怀疑这里。

未配置远程模型时，`/chat` 会使用本地检索摘要，适合离线演示和自动化测试。测试套件本身不读 `.env`（见 `tests/conftest.py` 的 `hermetic_settings`），所以本机填了真实密钥也不会改变 `pytest` 的结果。

## 向量后端

向量生成是一个可切换的后端（`src/support_agent/services/semantic.py`），默认不依赖任何模型：

| 后端 | 维度 | 依赖 | 用途 |
|---|---|---|---|
| `hash` | 384 | 无 | 默认。CI 与无模型环境，只保证流程跑通 |
| `onnx` | 512 | `onnxruntime` + `tokenizers` + 模型目录 | 本地语义检索，离线、无 API 成本 |

切到语义后端：

```powershell
pip install -e ".[semantic]"
# 模型目录需包含 tokenizer.json 与 onnx/model.onnx，例如
#   models/bge-small-zh-v1.5/tokenizer.json
#   models/bge-small-zh-v1.5/onnx/model.onnx
```

```dotenv
EMBEDDING_BACKEND=onnx
EMBEDDING_MODEL_PATH=D:\path\to\models\bge-small-zh-v1.5
```

三点需要留意：

- **换后端必须重建并重新导入语料**，两者是不同的向量空间：
  `python scripts/ingest_smartpv.py --source <资料目录> --reset`。片段元数据里记了建库用的后端指纹
  （`hash:384` / `onnx:512`）；检索时若发现片段向量长度与当前查询不一致，语义那一路会记 0 并打警告，
  而不是拿截断出来的假分数排序。
- 配置写了 `onnx` 但依赖或模型缺失时会**直接报错**，不会静默退回哈希向量——那样检索结果会变得无法解释。
- `/health` 会回报当前生效的 `embedding_backend` 与 `embedding_dimension`。

模型文件不进仓库也不进镜像，需要另行准备（约 90MB）。

## 架构

![系统架构](docs/diagrams/architecture.svg)

分层的源码版本（可折叠、便于 diff）：

```mermaid
flowchart TB
    U[本地客户端 / Swagger / CLI] --> RL[按用户限流]
    RL --> SESSION[(会话与历史)]
    RL --> SAFE[提示词注入拦截]
    SAFE -->|命中，不调模型| BLOCKED[blocked]
    SAFE --> ANCHOR{带序列号 / 算式 / 工单词？}
    ANCHOR -->|否| GATE[语料范围判据 · 词汇层面]
    GATE -->|判为跑题，不调模型| CLARIFY[needs_clarification]
    ANCHOR -->|是，或判据未命中| LOOP[Agent Loop · 最多 5 轮]
    LOOP <--> MODEL[模型：远程 API 或本地规则模型]
    MODEL -. 本地模式 .-> GRAPH[LangGraph 路由图 · 可加检查点]
    LOOP --> TOOLS[工具白名单 · 进程内注册表]
    TOOLS --> RAG[混合检索：单字 + 二元字组 + 语义向量]
    TOOLS --> CALC[safe_calculate]
    TOOLS --> DEVICE[query_device]
    TOOLS --> TICKET[create_ticket · 写操作]
    RAG --> META[行业标签加减分：机型 / 协议 / 版本 / 场景]
    RAG --> CACHE[检索缓存 · 进程内或 Redis · 熔断降级]
    RAG --> DB[(SQLite · PostgreSQL 方言可用 · pgvector 未接 ANN 索引)]
    RAG --> CONFLICT[版本并存并列提示]
    TICKET --> CONFIRM[拦下不执行 · 签一次性确认令牌]
    CONFIRM --> TICKETS[POST /tickets 四道闸：签名 → 有效期 → 归属 → 单次使用]
    LOOP --> AUDIT[(审计日志)]
    TICKETS --> AUDIT
    EVAL[离线分层评测：检索 / 工具选择 / 最终回答] --> RAG
    EVAL --> LOOP
    MCP[MCP Server · 旁路] -. 只读 Tool / Resource / Prompt .-> DEVICE
```

模型负责「提出调用哪个工具」，服务端负责「允不允许、参数对不对、要不要人工确认」。两条前置判据（注入拦截、语料范围）都在调用模型之前执行，命中时模型一次都不调用。

### 一次 `/chat` 请求的时序

```mermaid
sequenceDiagram
    autonumber
    participant C as 客户端
    participant A as FastAPI /chat
    participant S as SupportAgent
    participant M as 模型
    participant T as 工具与检索
    participant D as 数据库

    C->>A: POST /chat {message, session_id} + 登录会话
    Note over A: 限流在最前面 —— 被拒的请求不建会话、不写消息、不调模型
    A->>S: respond(text, session_id, user_id)
    S->>D: 读会话与历史（必须在写入本轮消息之前）
    S->>D: 写入本轮用户消息
    S->>S: 提示词注入拦截
    Note over S: 命中即返回 blocked，模型一次都不调用
    S->>S: 结构化锚点？无锚点才做语料范围判定
    Note over S: 判为跑题即返回 needs_clarification，同样不调模型
    loop 最多 5 轮（模型申请 → 服务端校验执行）
        S->>M: 对话历史 + 工具说明书
        M-->>S: 工具调用申请
        S->>T: 校验参数 → 执行（独立超时 10s / 同工具参数连续失败 2 次即停）
        T->>D: 检索：读片段与全语料词频统计
        T-->>S: 命中片段或错误分类
    end
    S->>S: 终态判定（模型故障 → 待确认 → 检索为空 → 有依据 → 业务工具 → 模型）
    S->>D: 写助手消息、引用、审计（同一事务）
    S-->>C: status / answer_source / citations / conflicts / pending_action / retryable
    C->>A: POST /tickets {confirmation_token}
    Note over A: 验签 → 有效期 → 用户归属 → 单次使用，四道闸依次通过才建单
    A-->>C: 201 工单，或 400 / 403 / 409
```

详细设计见 [docs/architecture.md](docs/architecture.md)，切块参数对照实验见 [docs/chunking_experiment.md](docs/chunking_experiment.md)。

## API

| 接口 | 用途 | 关键行为 |
|---|---|---|
| `GET /health` | 运行状态 | 报出 `version`、`environment`、向量后端与维度、缓存后端与是否降级、限流额度；排查问题的第一站 |
| `POST /documents` | 导入 TXT、Markdown、PDF | 限制大小、校验类型、按 SHA-256 去重 |
| `POST /chat` | 会话和 Agent 工作流 | 模型选工具、服务端校验执行；注入前置拦截；检索、计算器、设备查询、工单意图；写操作只返回确认令牌；依据跨版本时并列返回 `conflicts`；问题落在语料范围外时返回 `needs_clarification` 与建议补充的信息 |
| `GET /sessions/{id}` | 查看会话 | 使用服务器验证的登录身份校验所有权；管理员可查看历史会话 |
| `POST /feedback` | 回答反馈 | 保存评分和备注 |
| `GET/POST /admin/users` | 管理 user 账号 | 仅 admin 可查看和创建；停用与重置密码会撤销旧会话 |
| `POST /tickets` | 创建模拟工单 | 必须提供十分钟内有效且未使用的确认令牌 |
| `POST /evaluations/run` | 运行离线评测 | 需用 `dataset_path` 指定评测集；检索、工具选择、最终回答三层各自统计通过率，并回报本次使用的模型 |

请求示例见 [docs/api_examples.http](docs/api_examples.http)。

### `/chat` 的响应协议

返回不只是一段 `answer`。`status` 说明这一轮的终态，`answer_source` 说明这句话的依据来自哪里，
`retryable` 告诉调用方值不值得重试：

| `status` | 含义 | 可能的 `answer_source` | `retryable` |
|---|---|---|---|
| `completed` | 正常完成 | `knowledge`（带引用）/ `tool`（设备、计算）/ `model`（模型自行作答，无引用） | `false` |
| `degraded` | 模型不可用，返回服务端兜底话术 | `fallback` | 暂时性错误为 `true`，鉴权或请求格式错误为 `false` |
| `pending_confirmation` | 写操作被拦下，等待人工确认 | `policy` | `false` |
| `needs_clarification` | 问题落在语料范围外，请用户补充设备型号或现象 | `policy` | `false` |
| `blocked` | 安全策略拦截，模型一次都没被调用 | `policy` | `false` |
| `failed` | 跑完了但给不出有效结果，例如检索没有任何依据 | `policy` | `false` |

终态一律由服务端判定：模型只负责提出工具申请和写一段话，它无权声明自己这一轮属于哪一种。
客户端据此渲染状态标签、决定是否弹确认框或提示重试，不必再去猜回答文本的含义。

`needs_clarification` 还会带上一个 `clarification` 字段，说明是哪条判据命中、建议补充什么：

```json
{"status": "needs_clarification", "answer_source": "policy",
 "answer": "这个问题我没能定位到对应的资料。换成本领域的说法，或者补一句设备型号、现象就能对上……",
 "clarification": {
   "reason": "missing_terminology",
   "hints": ["设备型号（如 SUN2000-100KTL-M1、LUNA2000）",
             "具体现象（故障码 / 出现时的工况 / 涉及的屏柜或回路）"]}}
```

`reason` 有两种：`missing_terminology` 是措辞对不上（口语化说法），`unknown_foreign_terms`
是查询里的外文词一个都不认识。两者的追问话术不同，前者提示换领域说法，后者先说明这份资料不覆盖。

响应里还有一个 `conflicts` 字段，见下节。

## 行业元数据与版本并列提示

光伏资料的难点不在「找不到相似文字」，而在**相似文字可能不适用于当前设备**。
「户用」「工商业」「地面电站」讲同一件事用的词高度重合，不同版本的资料说法也可能不一样。
所以每个片段在导入时就带上行业标签，检索时先看标签再比相似度：

| 标签 | 例子 |
|---|---|
| `device_models` | `SUN2000`、`SUN2000-100KTL-M1`、`LUNA2000` |
| `protocols` | `Modbus TCP`、`RS485`、`IEC 104` |
| `document_version` | `V2.0`、`第3册` |
| `scenario` | `户用`、`工商业`、`地面电站`、`交直流屏柜` |
| `content_type` | `reference`（速查索引）、`procedure`（安装调测维护）、`faq` |

标签全部由词典和正则从**片段正文**确定性抽出，抽不到就留空——不写「可能是 SUN2000」这种模糊值。

检索时标签**只影响排序，不做硬过滤**：查询写了机型或场景，同类的加分、明确属于别类的降权。
实测「户用逆变器怎么组网」在加权前第 2～5 条混着「地面电站逆变器产品」，
加权后收敛到户用的 2.2 / 2.6 组网章节。

**当一次回答的依据来自两份不同版本的资料时，系统并列列出而不是替用户挑一个：**

```json
{"status": "completed", "answer_source": "knowledge",
 "conflicts": [
   {"document_version": "V2.0",  "filenames": ["M3-智能光伏工商业储能N+1交付指导.md"], "score": 0.6406},
   {"document_version": "第3册", "filenames": ["第3册-交直流屏柜原理.md"],           "score": 0.5898}]}
```

页面上表现为一条提示条，列出各版本的说法与出处，提醒按现场型号、固件和厂家正式资料确认。
这个字段的语义是「依据跨了版本，需要你裁决」，不是「系统判定两份资料矛盾」。

## 离线评测

`POST /evaluations/run?dataset_path=<数据集路径>` 跑一次三层评测，**声明了哪层就评哪层**，
没声明的层不进分母，因此不会拿「没写期望」的样本去拉低某层的分数：

| 层 | 评什么 | 判据 |
|---|---|---|
| `retrieval` | 检索质量 | Top-K 片段是否覆盖 `expected_keywords` |
| `tool` | 工具选择 | `expected` 里的工具都被选到，`forbidden` 里的一个都没碰 |
| `answer` | 最终回答 | `status` / `answer_source` 是否符合期望，回答是否含（或不含）指定文本 |

```json
{"id": "tool-01", "question": "啥是合母",
 "retrieval": {"expected_keywords": ["合母"]},
 "tools": {"expected": ["search_knowledge_base"], "forbidden": ["calculator"]},
 "answer": {"status": "completed", "answer_source": ["knowledge"]}}
```

字段全部可选；顶层直接写 `expected_keywords` 等价于 `retrieval.expected_keywords`，旧的检索评测集不用改。
只声明 `retrieval` 的样本不调用模型，一旦声明了 `tool` 或 `answer` 就会真的跑一轮 Agent Loop。

两层判定都不另写一份：检索层调 `SupportAgent.retrieve`，工具层和回答层调 `SupportAgent.run_turn`——
和 `/chat` 用的是同一份代码。自己抄一份判定，两边迟早会各自漂移，最后「评测通过」和「线上行为正确」说的不是一回事。

响应同时返回 `model`（本次用的模型，不同模型的分数不可比）、样本口径的 `total/passed/score`（三层全过才算一条通过）
和分层口径的 `layers`。写到 `failed` 层时还会带上可读原因，例如
`status=completed，期望 pending_confirmation` 或 `回答缺少：合母`。

## 版本与发布

版本号只有一处来源：`src/support_agent/__init__.py` 的 `__version__`。`pyproject.toml` 通过 hatch
动态读取它，`/health`、OpenAPI 文档与 `smartpv-agent version` 引用的也是同一个值——不存在「文档写了 1.0.0、运行时还是 0.1.0」。

版本号怎么变，看调用方会不会被打断：

| 变更内容 | 版本位 |
|---|---|
| `/chat` 响应协议、接口路径或配置项有不兼容改动 | 主版本号 |
| 新增能力，旧调用方无需改动 | 次版本号 |
| 向后兼容的修复与文档修正 | 修订号 |

发布一次的动作是固定的四步：

```powershell
# 1. 改 src/support_agent/__init__.py 的 __version__
# 2. 在 CHANGELOG.md 顶部按 Keep a Changelog 加一段，实测数字一并写上
python -m build                      # 3. 产物在 dist/（wheel + sdist）
git tag v1.0.0                       # 4. 打标签，与 CHANGELOG 的版本号一一对应
```

## 持续集成

每次推送与 PR 会跑四个 job：

| job | 内容 |
|---|---|
| `lint` | `ruff check --no-cache .` |
| `test` | Python 3.11 / 3.12 / 3.13 矩阵跑全量测试（带覆盖率，门限 80%）；主版本上额外跑一次 `doctor` |
| `package` | `python -m build`，把 wheel 装进干净虚拟环境跑 `version` / `doctor`，再起服务打 `/health` 与 `/` |
| `docker` | 构建镜像、启动容器，同样探测 `/health` 与 `/` |

后两个 job 存在的理由是具体的：`static/` 靠 hatch 的 `force-include` 进 wheel，Dockerfile 里 COPY 的顺序错过一次，
两条路径都属于「源码目录里全绿、一打包就坏」，只有在 CI 上才拦得住。

## 仓库导航

- `CHANGELOG.md`：版本历史，每条改动都附实测数字
- `CONTRIBUTING.md`：环境搭建、代码约定、提交前检查，以及「改了 A 必须同步 B」的连带清单
- `SECURITY.md`：已实现的安全边界、部署前必改项和已知限制
- `docs/deployment.md`：部署到服务器、升级与回滚、上线检查清单
- `LICENSE`：MIT
- `src/support_agent/`：应用代码
- `src/support_agent/cli.py`：命令行入口，`version` / `doctor` / `serve` 三个子命令
- `src/support_agent/services/cache.py`：检索缓存、按用户限流的四层存储（进程内 / Redis / 熔断降级）
- `src/support_agent/services/semantic.py`：可切换的向量后端（哈希兜底 / 本地 ONNX 语义模型）
- `src/support_agent/services/agent_loop.py`：手写「模型 → 工具 → 结果 → 模型」循环，含工具白名单、参数校验、轮数上限与写操作拦截（已接入 `POST /chat`）
- `src/support_agent/services/rag.py`：混合检索、行业元数据加权与跨版本并列检测
- `src/support_agent/services/industry.py`：机型 / 协议 / 版本 / 场景 / 片段类型的确定性抽取
- `src/support_agent/services/local_model.py`：无 API Key 时使用的本地规则模型，与远程客户端实现同一个 `chat_with_tools` 接口
- `static/index.html`：本地客户端页面（单文件，无构建步骤）
- `scripts/start_client.py`：一键启动客户端，负责选端口、开浏览器、探测模型连通性
- `scripts/create_desktop_launcher.py`：在桌面生成双击即用的启动器
- `scripts/ingest_smartpv.py`：把本地知识库目录导入当前 `DATABASE_URL` 指向的库，切块参数取 `CHUNK_SIZE` / `CHUNK_OVERLAP`，也可用 `--chunk-size` / `--overlap` 临时覆盖
- `scripts/ingest_document.py`：追加单份零散文档（docx / md / txt）到同一语料库，docx 正文不依赖第三方库
- `scripts/chunking_experiment.py`：切块参数对照实验，逐组重建索引并算命中率，结论见 [docs/chunking_experiment.md](docs/chunking_experiment.md)
- `tests/`：安全、RAG、Agent Loop、API 和工作流测试
- `docs/diagrams/architecture.svg`：系统架构图
- `docs/demo_script.md`：3–5 分钟演示的分镜与旁白稿
- `examples/manual_agent.py`：不依赖 Agent 框架的工具调用边界示例
- `examples/agent_loop_demo.py`：手写 Agent Loop 演示（脚本化假模型，无需 API Key）

知识库语料不进仓库：它随资料更新而变，且可能包含内部材料。评测集同理，由调用方在运行评测时指定路径。

导入语料有两条通道，都写进当前 `DATABASE_URL` 指向的库、共用同一个 `corpus_id`，因此追加的资料和原有语料一起被检索：

- 整批分卷：`python scripts/ingest_smartpv.py --source "<资料目录>"`（需 `index.json` + `知识库分卷/` 结构）
- 零散单份：`python scripts/ingest_document.py --file "<文件>" --document-id V3 --title "<标题>" --out "<存档 .md>"`
  支持 docx / md / txt；docx 按 `Heading1/2` 映射成 Markdown 章节后切分。先加 `--dry-run` 可只预览章节切分。重复导入同一份内容会按校验和跳过。

行业标签在导入时写入片段。**改了抽取规则或元数据字段后，需要清空 `document_chunks` 与
`source_documents` 再重导**——导入按校验和去重，内容没变就会整份跳过，旧标签不会自己更新。
表结构本身没变，不必重建库文件。分卷导入的版本号从 `index.json` 读，单份导入用
`--document-version` 指定（默认从标题里抽，例如「第3册」）。

## 使用边界

本服务用于内部技术支持场景，语料与评测集都不进仓库，随资料更新由调用方导入；导入方式见上面的两条通道。

演示与测试环境使用本地示例数据与模拟工单，**不接真实客户数据，也不接真实支付或退款操作**。
部署前需要落实的事项见 [SECURITY.md](SECURITY.md)，上线检查清单见 [docs/deployment.md](docs/deployment.md)。
