# 更新日志

## [1.10.3] - 2026-10-08

- 现场截图在检索前提取客户诉求、设备型号、通信地址和约束，分离界面/铭牌背景；原始识别文字与会话输入保留。表格数值只作为待核对的补充信息，不用于拼接确定的测点映射。
- 修复铭牌 `44/44/44/44`、日期等参数误触发计算流程；计算工具与语料范围判断统一使用计算意图规则。
- 为问答资料标注失败/未解决状态；本地摘要排除失败记录，生成回答过滤将已知无效操作当作建议的情况。
- 数据不刷新且缺少依据时，提示补充采集时间、本机读数、采集器状态与通信方式。

## [1.10.1] - 2026-10-08

- 重写 GitHub 主页介绍，按业务功能、本机安装、资料处理和当前使用边界组织内容，移除首页过时指标和个人资料路径。
- 补齐首次安装的 admin 初始化步骤，明确普通/隐私资料问答均可能外发、工具上下文与公开搜索的实际范围。
- 修正示例配置中的旧本地模型说明及软件成熟度标记；本版本不改变业务行为。

## [1.10.0] - 2026-10-08

- 新增管理员管理中心：运行概况、资料版本与分库管理、资料停用/启用、分页操作记录和备份下载；user 无管理权限。
- 停用资料从后续检索、缓存和刷题资料中排除；新上传原件按校验值留存在本地资料库，历史会话引用保留。
- 统一输入中的刷题结果保存为会话消息，可继续提问和反馈；普通现场消息不再被 Agent 内部宽泛的选项规则重新误分到刷题。
- 新增在线 SQLite 一致性备份、文件校验和恢复到新目录的工具；恢复库撤销旧登录会话。
- 增加数据库就绪探测、轮转运行日志、正式环境认证与密钥配置检查、持久化运行目录和独立客户端环境准备工具。

## [1.9.0] - 2026-09-30

- 文字与图片改走统一输入入口。上传或粘贴截图后先在本机识别文字，再按内容判断为学习题目或现场问题；客户消息截图不再直接进入刷题接口。
- 图片识别结果可展开核对并修改后重新提问；文字题目也可使用现有刷题讲解，原有 `/chat` 与 `/quiz/analyze` 接口继续兼容。
- 更新桌面启动脚本到当前项目与本地依赖路径，启动时立即显示等待提示；修复旧入口仍指向已移走项目的问题。

## [1.8.1] - 2026-09-30

- 点表制作改为选南向或北向后上传设备协议 Word/PDF，在本机解析候选点位，预览、补充现场字段和逐点核对后导出 CSV。
- 不再把旧智能空开案例当作通用固定模板；北向点号必须另行填写，设备协议中的 Modbus 地址不会自动套用为北向点号。
- 支持文字版 PDF、Word 表格和部分旧 `.doc` 协议；扫描件及无法识别的版式明确提示人工处理。

## [1.8.0] - 2026-09-30

- 点“点表制作”可从本机资料库选择智能空开南向 27 点或北向实验室 19 点模板；南向模板提示填写现场通道、设备身份、从站地址与电站编号，完成后下载 CSV。
- 南向模板保留旧案例按协议核对的 24 列点位结构，清除旧现场的通道、序列号等值；生成时校验表头、地址与关键协议列，不猜造缺失点位。旧实验中的累计电量仍需现场对点。
- 点表模板只保留在本机被 Git 忽略的 `knowledge_base/点表规则`，生成接口需要登录，输出不在服务端保存。

## [1.7.1] - 2026-09-30

- 修复管理 user 账号弹窗中创建按钮占满整行、将登录名输入框挤成竖排的问题；窄屏时表单改为上下排列。

## [1.7.0] - 2026-09-30

- admin 可在页面创建 user 账号、停用/启用账号及重置密码；初始密码只显示一次，停用或重置会撤销已有登录会话。
- user 登录后可问答、图片答题、反馈、创建自己的工单并查看自己的会话；资料导入、评测和账号管理仅限 admin。
- 所有业务接口继续使用服务端验证的身份，浏览器传入其他用户编号或会话编号不能越权；管理员账号不能被 user 管理接口修改。

## [1.6.1] - 2026-09-30

- 管理员密码的最短长度由 12 个字符调整为 8 个字符，首次创建和页面改密使用同一要求。

## [1.6.0] - 2026-09-30

- 新增管理员账号、密码登录、服务器会话及退出和改密；首次账号由 `scripts/create_admin.py --generate` 创建。
- 服务端以登录身份处理聊天、会话、反馈和工单，忽略浏览器自报的用户编号；资料导入与评测仅限 admin。
- 页面增加登录和改密界面；未登录不能调用业务接口，写请求需携带会话校验值。

## [1.5.2] - 2026-09-30

- 按用户最新授权，普通和隐私问答都由 DeepSeek 整理；隐私问答仅发送当前问题和最多两段命中资料，不附带聊天历史。图片答题的模型兜底同样改用 DeepSeek。
- 本地轻量模型退出默认问答流程；远程模型不可用时，有明确本地依据的问答仍返回摘录。

## [1.5.1] - 2026-09-30

- 本地客户端启动时自动寻找并启动已安装的 Ollama，关闭客户端时结束由它启动的进程。
- 健康检查新增本地模型实际连通状态，页面区分“已连接”和“未启动”。
- 本地模型请求不读取系统代理设置，确保回环请求留在本机。
- 本地 Qwen3 问答关闭耗时的思考输出，限制回答长度及单次等待时间；本地检索依赖缺失时返回可读的服务状态，不再直接显示 HTTP 500。
- 本地资料命中同类故障且原文紧跟“解决方法”时，直接列出处理步骤；其他问题缩短送入本地模型的资料长度，减少等待。

## [1.5.0] - 2026-09-29

- 将资料按普通/隐私片段隔离；旧资料默认隐私，仅人工选定的公开手册和 HCSA 通用片段进入普通库。
- 启用隐私分流后，普通问题只把普通库命中片段送给 DeepSeek，不附带聊天历史；隐私问题只调用本地模型。
- 隐私问题本地无依据时，仅从产品名和通用故障词构造公开搜索词；搜索服务和本地模型未配置时明确拒答，不会转发原问题。
- 新增本地 OpenAI 兼容模型接口、可选的公开网页搜索接口与资料分类预览脚本。

### 刷题链路（同日补录）

- **聊天框也能答题**：`agent.respond` 增加 `_try_quiz` 分流，消息能被解析出「题干 + 至少两个选项」时直接走刷题判定，回答是「结论 选 X」而不是一段不给结论的话术。
- **判题接上模型**：`quiz.answer_question` 新增 `llm_client`，仅在本地规则、题库、检索证据都判不出时才调用；模型被强制只依据本地原文、只返回 JSON，字母必须落在给定选项内，异常一律降级为「依据不足」。
- **检索多查询化**：完整题干带「正确的操作顺序是」一类套话会稀释关键词权重（实测同一段原文 0.40 vs 精简后 0.62）。现在同时用完整题干与去套话的精简版检索，合并去重后按分数排序。
- **题库补录至 5 题**（`knowledge_base/_converted/HCSA-V2-补充题解析.md`，答案逐条对照华为官方文档核实）：SmartLogger MBUS 接入功率 >75kW、NetEco1000S 配置要求（全选）、长组串设计（优化器满配）、SUN2000 L1 电池与电表信号线端子（共用 485A2/485B2）、建站向导快速建站顺序（设备发现 → 参数设置 → 连接管理系统）。
- 新增逐项检索消歧：单选题用「题干 + 选项」逐项检索，分数领先时才选；分数分不开时宁可提示依据不足，不猜测。

本文件记录对外可见的变化。格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

版本号怎么变，看的是调用方会不会被打断：

- 主版本号：`/chat` 的响应协议、接口路径或配置项有不兼容改动；
- 次版本号：新增能力，旧调用方不用改；
- 修订号：向后兼容的修复与文档修正。

`1.0.0` 之前的版本号是按四个落地阶段回填的（仓库从最早的骨架长到现在，早期没有打标签）。
保留分段是为了能回溯「哪一次改动带来了哪一项指标变化」——每个版本下的 `实测` 一节记的就是这件事。

## [1.4.1] - 2026-09-29

修复图片答题的三个实测问题（用三张题图端到端验证）：

### 修复

- **OCR 选项字母拆行**：识别结果里字母单独成行（"C" 一行、"25kW" 一行）时，原来字母会混进题干、选项丢失。现在挂起字母并与下一行合并，`A`–`F` 全部恢复。
- **多选题丢字母**：多选题图 OCR 经常丢掉后面的选项字母（NeteCo1000S 配置题只剩 A、B 带字母）。多选题按字母顺序顺延补齐；单选题不启用，避免选项折行被误拆。
- **`question_type` 协议违约**：`parse_question` 返回的 `"选择"` 不在 `QuizResponse.question_type` 的 `Literal["判断","单选","多选","未知"]` 里，解析成功的请求会因响应校验失败而 500。现在返回 `"单选"`/`"多选"`。
- **题库匹配支持多选**：`_bank_match` 原来要求恰好一个选项文字命中参考答案，多选题必然匹配失败。现在多选题允许多个命中一起返回；单选题保持原约束。

### 新增

- 题库补录三道实测缺失题（`knowledge_base/_converted/HCSA-V2-补充题解析.md`，答案逐条对照华为官方文档核实）：SmartLogger MBUS 接入功率 >75kW、NetEco1000S 配置要求（全选）、长组串设计应用场景（优化器满配）。题库匹配的文档过滤放宽为 `*题解析*`，补充题解析与模拟题逐题解析同库参与匹配。

## [1.4.0] - 2026-09-29

- 新增本地图片答题：上传或粘贴截图，识别题干与选项，展示选项、简短理由和资料依据；识别文字可修改后重答。
- 图片识别在本机进行，默认关闭远程模型。证据不足时明确提示，不猜测答案。
- 页面只保留具体业务入口，并改进本地资料回答的摘要排版。

## [1.3.2] - 2026-09-18

仓库改名同步，**没有任何代码行为变更**：接口、配置项与默认参数照旧，`/chat` 的响应结构不变。

GitHub 仓库由 `agent-ai-8week` 改名为 `smartpv-support-agent`。新地址 `https://github.com/Garcia-rgb/smartpv-support-agent`
（旧地址仍会 301 跳转），本版把这个名字在仓库里的每一处引用都跟上。

### 变更

- **自指链接**：`CHANGELOG.md` 的 13 条 compare / release 链接、README 的 clone 地址、
  `pyproject.toml` 的 Homepage / Repository / Issues / Changelog / Documentation、
  `SECURITY.md` 的 issue 入口、`Dockerfile` 的 `org.opencontainers.image.source` 标签，全部改为新地址。
  这些是唯一会跟着远端改名而失效的东西——旧地址只是靠跳转还能打开。
- **环境名**：`environment.yml` 的 `name` 与 README / CONTRIBUTING 里的 `conda activate` 由
  `agent-ai-8week` 改为 `smartpv-support-agent`；README 的 clone 后 `cd` 也改为新目录名
  （`git clone` 默认按仓库名建目录）。
- **容器名引用改为 compose 服务名**：README 与 `docs/deployment.md`、`scripts/check_schema.py`
  里的 `docker exec -i agent-ai-8week-api-1 ...` 改为 `docker compose exec -T api ...`，
  Redis 那处同理改为 `docker compose exec redis ...`。原先写死的容器名带 compose 项目名前缀，
  而项目名默认取目录名——只在「目录恰好叫 `agent-ai-8week`」时成立，别人 clone 下来就已经不对了。
  改用服务名后与项目名无关，这次改名和以后换目录都不会再让它失效。

### 实测

- 206 passed、1 skipped（共 207 项收集），与 1.3.1 持平；Ruff 通过。
- 除本节对改名本身的说明外，仓库内 `agent-ai-8week` 的引用数：30 处 → 0 处。
- `python -m build` 重新出包：`smartpv_support_agent-1.3.2-py3-none-any.whl`，
  元数据的 `Project-URL` 五项均为新地址，`static/` 前端资源仍在包内。

## [1.3.1] - 2026-09-18

仓库内容整理，**没有任何代码行为变更**：`/chat` 的响应结构、接口路径、配置项与默认参数全部照旧。

### 变更

- **移除与产品无关的仓库内容**。此前仓库里混着 34 个文件不属于这个服务本身：一份按周排的学习路线、
  一套按章组织的面试题、8 周计划交接文档、12 份周次日程、9 份分阶段练习、模拟面试问答集、
  面试题库、简历模板、求职投递追踪表、技术栈手册、每周复盘与错误日志模板，以及依赖练习目录的
  4 项测试。它们已从仓库移出并另行归档，不再随代码分发。
- **统一文档口吻**：README、CONTRIBUTING、SECURITY 的定位表述改为描述这个服务本身做什么，
  不再把它称作学习或求职用途的材料；OpenAPI 描述（Swagger 页面首段）同步改写。
- **清掉源码与文档里的阶段编号**。约 20 处注释、docstring 与小节标题写着「第 N 周 Day X」这类
  排期编号（`services/agent_loop.py`、`services/agent.py`、`mcp_server.py`、`docs/mcp.md`、
  `docs/state_graph.md`、三个 `examples/` 脚本与两个测试模块等），改为直接说明该段代码解决什么问题。
  编号对读者没有信息量，且与「哪一步做了什么」的 CHANGELOG 记录重复。
- **`pyproject.toml` 的 `pythonpath` 注释**去掉对已移出目录的引用，保留设置本身：
  这条约束与具体目录无关，`pytest` 控制台脚本与 `python -m pytest` 的导入路径仍需一致。

### 实测

- 206 passed、1 skipped（共 207 项收集；上一版 210/211，差额正是移出的 4 项测试）。
- 覆盖率 88.84%，与上一版持平；Ruff 通过。
- 全仓库相对链接复查：没有指向已移出文件的死链。

## [1.3.0] - 2026-09-18

把「切块用 700/100」从拍的变成试过的，同时补齐交付与展示件。`/chat` 的响应结构、接口路径与默认参数都没有变化。

### 新增

- **切块参数对照实验**（`scripts/chunking_experiment.py`、`docs/chunking_experiment.md`）。
  同一份语料、同一份 40 条带标注的评测集、同一套检索参数，只换 `chunk_size/overlap`：
  300/50 跑出 446 片段 / `hit@1` 0.625，700/100 跑出 207 片段 / 0.700，1200/150 跑出 143 片段 / 0.700。
  每组参数用独立的一次性数据库，互不共享索引。脚本支持 `--configs` 指定多组、`--report` 只汇总。
- **系统架构图**（`docs/diagrams/architecture.svg`）与 README 里的一次 `/chat` 请求时序图（mermaid）。
  架构图按入口 / 接入 / 决策 / 能力 / 存储 / 旁路六层排布，把两条前置判据、熔断降级、四道闸都画了出来。
- **演示脚本**（`docs/demo_script.md`）：七段分镜、逐段旁白、演示前准备与录完自检。
- **表结构漂移自检**（`scripts/check_schema.py`）：逐表对比代码里的列定义与数据库里实际的列。
  `create_all` 只补建缺失的表、不改已存在表的列名，所以把某个列改名（如 `tickets.order_id` →
  `tickets.device_sn`）之后，SQLite 那边重建过库就没事，PostgreSQL 走命名卷则会一直停在旧结构，
  直到某次写入才炸出 500。脚本列出差异并给出候选的 `ALTER TABLE ... RENAME COLUMN` 语句，
  有漂移时退出码为 1，可直接串进部署前检查。
- **交付文档三件套**：`CONTRIBUTING.md`（环境搭建、代码约定、提交前检查，以及「改了 A 必须同步 B」的连带清单）、
  `SECURITY.md`（已实现的安全边界、部署前必改项、已知边界）、`docs/deployment.md`（部署、升级与回滚、上线检查清单）。
  此前 README 只覆盖「在开发机上跑起来」，缺的部分全靠口口相传。
- **CI 覆盖面**（`.github/workflows/ci.yml`）：此前只有 Python 3.12 的 ruff 与 pytest。
  现在加了 3.11/3.12/3.13 矩阵；`python -m build` 之后把 wheel 装进干净虚拟环境再冒烟一遍
  （`version` / `doctor` / 起服务打 `/health` 与 `/`）；以及 Docker 镜像的构建、启动与同样的两项探测。
  后两条正是历史上真实坏过的路径——`static/` 没进 wheel、Dockerfile 里 COPY 顺序错导致构建失败。
- **CLI 测试**（`tests/test_cli.py`）。`cli.py` 此前是零覆盖：它是 `smartpv-agent` 的对外入口，
  退出码被部署脚本依赖，却一行测试都没有。
- **覆盖率门限 80%**（`pyproject.toml` 的 `[tool.coverage.report]`），防止覆盖率静默下滑。
- **容器加固**：镜像改为非 root 用户运行，并把 `/app` 所有权一并交出——只降权不交权的话，
  SQLite 形态连默认的 `sqlite:///./support_agent.db` 都建不出来；compose 三个服务加 `restart: unless-stopped`。

### 修复

- **`scripts/ingest_smartpv.py` 忽略 `CHUNK_SIZE` / `CHUNK_OVERLAP` 配置**。
  它此前直接用 `RAGService` 的构造默认值（700/100）当参数，于是 `.env` 里改了切块参数只会影响别处、
  导入仍按 700 切——属于「看着生效、其实没生效」的一类。现在读 `settings.chunk_size / chunk_overlap`，
  并额外开了 `--chunk-size` / `--overlap` 两个开关供对照实验逐组重建索引。
- **`Dockerfile` 里 `COPY static` 的位置导致镜像构建失败**。`pyproject.toml` 用 hatch 的 `force-include`
  把 `static/` 打进 wheel，而该目录原先是在 `RUN pip install .` **之后**才 COPY 进镜像的，
  构建元数据阶段找不到它就直接失败（`FileNotFoundError: Forced include not found: /app/static`）。
  把 `COPY static ./static` 移到 `pip install` 之前即可。这条路径自加上 `force-include` 之后
  就没有再构建过，所以一直没有暴露。
- **`doctor` 把警告也当成失败**（`cli.py`）。`_render` 原先按 `status != OK` 收集问题，
  于是「没配远程模型」——一个**受支持的默认模式**（退回本地规则模型）、新克隆的仓库和 CI 都长这样——
  也会让退出码为 1。README 一直承诺这条命令「可直接串进 CI 或部署脚本」，实际一跑就红。
  现在只有 `FAIL` 返回非零，警告照常打印但不影响退出码；需要「全绿才算过」的部署门禁加 `--strict`，
  它会把警告一并升级为失败。
- **`.env.example` 缺 5 个配置项**：`RETRIEVAL_CORPUS_ID`、`CHUNK_SIZE`、`CHUNK_OVERLAP`、
  `EMBEDDING_BATCH_SIZE`、`EMBEDDING_MAX_LENGTH`。照着模板拷出来的 `.env` 看不出这些旋钮存在，
  而其中既决定检索范围也决定切块粒度。
- **`pytest` 控制台脚本与 `python -m pytest` 的导入路径不一致**（`pyproject.toml`）。前者不把当前目录
  放进 `sys.path`，后者会。仓库根目录下有不属于已安装包的模块时，本地怎么跑都是绿的，CI 一跑就是
  `ModuleNotFoundError` 加收集中断（`collected 207 items / 3 errors`，退出码 2）。
  现在在 `[tool.pytest.ini_options]` 里显式声明 `pythonpath = ["."]`，两种调用方式结果一致。
  这个缺口只有在 CI 真跑起来之后才会暴露：本地要么用 `python -m pytest`，要么根本不装控制台脚本。

### 实测

- 三组参数的 `hit@1` / `hit@3` / `hit@5` / `MRR` 见 `docs/chunking_experiment.md`。结论是：细切块明显更差，
  粗切块与当前默认值的差距在 1–2 条样本内且方向不一致，因此默认参数维持 700/100。
- 700/100 这一组复现了开发库现状（207 片段 / `hit@1` 0.700 对 212 片段 / 0.700），说明实验口径可信。
- 三组返回空结果的样本是**完全同一批 7 条**，说明语料范围判据与切块粒度无关。
- Docker Compose 路径实测（Docker Desktop 4.90.0 / Engine 29.7.2）：
  `docker compose up --build -d` 起三个容器全部 healthy；容器内 `/health` 报 `version=1.3.0`、
  `embedding_backend=hash`、`cache={backend: redis, primary: redis, degraded: false}`，说明 PG 与 Redis
  都真的接上了；写操作四道闸在 PostgreSQL 事务下依次返回 201 / 409 / 400（签名无效）/ 403（归属不符）
  / 400（格式无效）；连续 30 次 `/chat` 返回 200，第 31 次返回 429 并带 `Retry-After: 6`；
  `docker compose down` 再 `up -d` 之后工单、消息与令牌消费记录全部保留。语料按上面的方式导入后，
  容器内 20 文档 / 212 片段与本地 SQLite 完全一致。
- 210 passed、1 skipped（共 211 项收集，跳过的那项需要本地模型文件），Ruff 通过；
  覆盖率 83% → 88.84%（门限 80%）。新增的 12 项来自 `tests/test_cli.py`，补完后 `cli.py` 从 0% 到 78%。
- 本版首次推送后 CI 真实结果：`lint`、`package`、`docker` 三个 job 通过（wheel 装进干净环境与
  容器内两条路径都验到了 `/health` 与 `/`），`test` 矩阵因上面那条收集错误失败。
  按 CI 的调用方式（`pytest` 控制台脚本）本地复跑修复后的结果：210 passed / 1 skipped、
  覆盖率 88.84%，与 `python -m pytest` 一致。

## [1.2.1] - 2026-09-17

修掉确认令牌链路上的两个缺陷。都属于「代码看着对、行为不对」的一类，是补测试时暴露出来的。
`/chat` 的响应结构与接口路径没有变化。

### 修复

- **防重放从「先查再写」改成「写入即判定」**（`main.py`、`models.py`）。
  原来是在 `audit_logs` 里查一条 `confirmation_consumed` 记录，查到就返回 409；检查与写入之间有时间窗，
  并发下两个请求都能查到「没人用过」，于是同一张令牌建出两条工单。现在新增
  `consumed_confirmation_tokens` 表并把 `token_hash` 做成主键：重复写入直接撞唯一约束，
  判定与占位压成一次原子操作。两个附带的性质是有意的——消费记录与工单在同一事务里提交，
  不会出现「令牌已烧掉、工单却没建」；判据也不再寄居在审计表里，审计记录可以清理，防重放不会跟着失效。
- **`verify_confirmation_token` 里的「签名无效」曾经是死代码**（`services/security.py`）。
  签名比对写在 `try` 块内部，抛出的 `ValueError` 被同一个 `try` 的 `except ValueError` 接住，
  重包成笼统的「确认令牌无效」，于是这个分支调用方永远看不到。原来的用例用 `match="无效"` 松匹配，
  正好把它掩盖了过去。现在签名比对移到 `try` 之外，三种拒绝原因分得开：格式无效 / 签名无效 / 已过期。
  另补两处守卫——正文不是对象、`exp` 不是数字时同样抛 `ValueError`；否则 `payload.get` 会抛
  `AttributeError`、`int()` 会抛 `TypeError`，而调用方只接 `ValueError`，结果是 500 而不是 400。

### 新增

- `examples/confirmation_token_demo.py`：四段演示，正常令牌可用 / 篡改被拒 / 重放被拒 / 过期被拒。
  数据落在临时库，不连模型也不加载 ONNX 模型，跑多少遍结果都一样。
- 测试：`tests/test_security.py` 2 → 7 例（补改动正文的伪造、过期、换密钥、格式错、内容不可用），
  `tests/test_api.py` 补过期令牌与唯一约束两例，并让重放用例顺带验证消费记录只存哈希、与工单挂钩。

### 实测

- 全量 191 → 198 passed，Ruff 通过。
- `python examples/confirmation_token_demo.py`：四段依次得到 201 / 409（确认令牌已经使用）/
  400（确认令牌签名无效）/ 400（确认令牌已过期）。第三段用的那张令牌在第二段已经被消费过，
  返回的仍是 400 而不是 409——验签排在判重之前，改过的令牌不会被判成「已使用」，
  也就不会泄露这张令牌是否存在。

### 说明

- 消费记录只存 `token_hash`，不存令牌原文：令牌本身就是凭证，落库等于多留一份可用的口令。
- 这张表暂时没有清理策略。生产上按 `created_at` 定期删即可，窗口取令牌有效期长度——
  删掉的只是「已用过的哈希」，而令牌本身过了 `exp` 就验不过了。

## [1.2.0] - 2026-09-17

本版两项能力：把设备能力以 MCP 协议暴露出去，以及给工具调用补上容错。
两项都向后兼容，`/chat` 的响应结构与接口路径没有变化。

### 新增

- **MCP Server**（`support_agent.mcp_server`，入口 `python -m support_agent.mcp_server`）。
  只读不写：一个 Tool `query_device`、一个 Resource `device://catalog`（只读设备清单）、
  一个 Prompt `fault_report`（故障上报模板）。Client 示例见 `scripts/mcp_client_demo.py`，
  跨进程集成测试见 `tests/test_mcp.py`。
  业务失败（查不到设备）按协议报成 `isError=True`，同时保留可读的错误文本。第一版把它写成
  普通返回值，Client 收到的是 `isError=False`——Host 从协议层看不出这次调用失败了，
  想做「工具失败率」只能去读文本猜。协议层给信号、文本层给细节，两者都要。
- **工具容错三件**（`services/agent_loop.py`）：
  - `ToolSpec.timeout_seconds`（默认 10 秒；要查库的 `search_knowledge_base` 放宽到 15 秒）。
    同步 handler 先丢进线程再等，否则这次调用会占住事件循环，`wait_for` 的计时器根本不触发
    ——写成超时、实际不生效。
  - 同一个「工具 + 参数」连续失败 `MAX_TOOL_RETRIES`（2）次后服务端不再放行，
    第三次直接返回 `repeated_failure`。这不是补能力，是控成本：模型把「工具失败」理解成
    「再试一次」时，一次提问会放大成几十次调用。
  - `ToolOutcome.error_kind` 与 `ToolCallRecord.error_kind`：失败分七类（`unknown_tool` /
    `invalid_arguments` / `needs_confirmation` / `timeout` / `tool_error` / `internal_error` /
    `repeated_failure`），审计与指标可以按类型统计，不必去猜错误字符串。

### 变更

- `graph.build_route_graph()` 新增 `checkpointer` 与 `interrupt_after` 两个可选参数。默认 `None`，
  行为与改动前完全一致（`local_model` 正在调用它，默认值不能变）；传入检查点后每一步状态都落盘，
  可以用同一个 `thread_id` 续跑，也可以配合 `interrupt_after` 停在某个节点之后等人工介入。
- `ROUTES` 常量抽出，节点注册与条件边共用一份，不再两处各写一遍。
- 依赖新增 `mcp>=2,<3`。注意 MCP 2.x 把 `FastMCP` 改名为 `MCPServer`、字段统一成 snake_case
  （`serverInfo` → `server_info`、`isError` → `is_error`），照 1.x 文档写会一路撞
  `ModuleNotFoundError` 与 `AttributeError`。

### 实测

- 全量 168 → 191 passed（新增 `tests/test_graph.py` 12 例、`tests/test_mcp.py` 5 例、
  `tests/test_agent_loop.py` 容错 6 例），Ruff 通过。
- `python examples/graph_checkpoint_demo.py`：`interrupt_after=["classify"]` 时第一次 `invoke`
  停在 `next=('ticket',)`，`invoke(None, config)` 续跑后 `next=()`。
- `python scripts/mcp_client_demo.py`：跨进程握手到 `smartpv-device`，Tools / Resources / Prompts
  三类都能列出并调用；查不存在的设备返回 `isError=True` 且错误文本可读。

### 说明

- 超时只让调用方不再等待，**不能中止已经在跑的同步工具**，所以「超时 ≠ 操作已回滚」。
  会写数据的工具必须自己支持取消或做成幂等——这也是写操作一律不自动执行的又一理由。
- MCP Server 目前没有配鉴权，入口留在 `MCPServer` 的 `auth_server_provider` 与 `token_verifier`。
  Authorization、用户同意、工具权限边界三者的归属见 `docs/mcp.md`。

## [1.1.3] - 2026-09-16

去掉回答里的元数据噪声。抗幻觉的判定一条没动——改的是「标签怎么进到回答里」。

### 修复

- 检索片段附带的行业标签不再罗列机型清单。真实语料里有片段一次列了 14 个型号，
  标签因此长达 224 字；而 `SYSTEM_PROMPT` 和 `search_knowledge_base` 的工具说明
  两处都要求「回答时带上这些标签」，模型便照抄进正文。实测一条绝缘阻抗排查的回答里，
  4 处标签共 581 字，占全文 1814 字的 32%。现在机型超过 3 个就归纳成「N 个机型通用」
  （`industry.MODEL_LIST_LIMIT`），少数几个机型仍逐个列出——那正是「这段只适用这几款」的关键信息。

### 变更

- `agent_loop.SYSTEM_PROMPT` 补上正向要求：直接给结论和可执行的步骤，不要交代检索过程、
  不要解释信息来源、不要复述资料标签；引用依据只用一句短标签带过。
  同时把「提醒用户按现场确认」收敛为只在真有版本差异或机型适用性限制时出现，
  不再每轮都附一段泛泛的免责。
- `search_knowledge_base` 的工具说明同步：那些标签是给模型自己核对依据是否适用用的，
  不是要抄进回答。

### 实测

- 同一问题「逆变器报绝缘阻抗低，现场怎么排查？」：回答从 1814 字（含 581 字标签）变为
  940 字且无标签罗列，改为直接给 7 步可执行排查（下电验电 → 查 PE 线 → 测对地绝缘 →
  查 MC4 接头 → 逐路定位 → 百分比换算并附算例 → 潮气导致的可调项）。
- 抗幻觉未退化：问「直流母线电压是多少」仍返回 `conflicts=2` 并列两种口径；
  设备查询仍为 `completed` / `tool`；机型适用范围差异仍在回答末尾点明
  （工商业版把该定位法标注为仅适用 SUN2000-12/15/17/20KTL-M2）。
- 测试 167 → 168 passed，Ruff 通过。

## [1.1.2] - 2026-09-16

清掉早期电商示例域留下的残迹，并修掉一个因评测集移出仓库而失效的脚本。
`/chat` 的行为没有变化。

### 修复

- `scripts/smoke.py` 调 `POST /evaluations/run` 时没带必填的 `dataset_path`，
  评测集移出仓库后这个脚本就一直拿不到结果（接口要求显式指定路径）。
  现在改为读环境变量 `SMOKE_DATASET_PATH`，没设置就跳过评测并在输出里说明。

### 变更

- `llm.py::ANSWER_SYSTEM_PROMPT` 的身份从「你是企业客服」改为「你是光伏电站技术支持工程师」。
  这条提示词只有 `scripts/check_llm.py` 的单轮问答自检在用（`/chat` 走 `agent_loop.SYSTEM_PROMPT`），
  所以对外行为不变，只是自检的回答口吻不再和业务域错位。
- 清掉各处遗留的电商示例：`check_llm.py` 的自检资料与提问、`smoke.py` 的问答与建单话术、
  以及四个测试文件里的「退款」夹具（一律换成光伏文本）。
- 措辞同步：`llm.py::local_answer` 的兜底话术改说「转人工支持」，
  `local_model.py` 的 docstring 改称「技术支持口吻」。

### 实测

- `scripts/smoke.py` 全流程通过：健康检查 → 知识问答（有引用）→ 工单确认 → 防重放 409 → 评测步骤按提示跳过。
- `scripts/check_llm.py` 通过：单轮问答依据给定片段作答，工具循环 2 轮，`calculator` 返回 5.0。
- 167 passed（1 skipped），Ruff 通过。

## [1.1.1] - 2026-09-16

修掉一个让结构化工具整个失效的缺陷：语料范围判据连工具类问题一起拦了。
带设备序列号的问法、建工单的诉求被判成跑题，直接返回追问，
`query_device` 与 `create_ticket` 因此永远走不到。修复是给判据加一层豁免——
带结构化锚点的问题不参与判定。

### 修复

- 新增 `services/tools.py::has_structured_anchor()`，识别三类锚点：设备序列号
  （`SN-2024-000123`）、算术表达式（`100*0.986`、`100 乘以 0.986`）、工单诉求
  （`工单` / `报修` / `派单` / `转人工` / `投诉`）。命中任一类，`agent.py` 的前置拦截
  就跳过语料范围判据，把问题交给模型去选工具。
- 判据本身一个字没改：跑题问题照旧被拦下，集成测试里留了对照组守着。

### 为什么改

判据问的是「这个问题属于这份资料吗」，而这几类问题由工具承接、压根不查这份资料，
拿同一把尺子量会量错。实测（212 片段真实语料）：

| 问法 | 词组缺失比例 | 修复前的结果 |
|---|---|---|
| `SN-2024-000123 这台设备现在什么状态` | 0.62 | `needs_clarification`，设备查询失效 |
| `SN-2024-000123` | 0.67 | `needs_clarification` |
| `设备 SN-2024-000123 现在是什么状态` | 0.50 | 放行——所以缺陷只在部分问法上显形 |
| `帮我建个工单` | 0.67 | `needs_clarification`，写操作入口不可用 |

同一件事换个说法就能过，说明拦下它的不是「话题不相关」，而是「措辞没对上语料」。

### 实测

- `SN-2024-000123 这台设备现在什么状态` → `completed` / `tool`，返回型号、额定功率、运行状态、固件版本。
- `SN-2024-000789 故障停机了，帮我建一个工单（含标题与描述）` → `pending_confirmation` / `policy`，带确认令牌。
- 对照组 `Python 怎么装环境` → 仍是 `needs_clarification` / `unknown_foreign_terms`，模型零调用。
- 测试 156 → 167 passed（锚点单元用例 10 条、集成用例 1 条），Ruff 通过。

## [1.1.0] - 2026-09-16

命中语料范围判据时不再拒答，改成请用户补充信息。这是对外可见的行为改变：
`/chat` 多了一个终态，客户端要认识它才能把追问渲染成输入引导。

### 新增

- 终态 `needs_clarification`（配 `answer_source=policy`、`retryable=false`）与响应字段
  `clarification`：`reason` 说明是哪条判据命中，`hints` 列出建议补充的信息。
- `RAGService.corpus_scope_reason()`：判据命中时连原因一起返回，不再只有一个布尔值；
  `is_out_of_corpus()` 保留为它的布尔形式，给只关心是/否的调用方。
- 客户端把 `hints` 渲染成「请补充：设备型号…」提示项，用户照着说一句就能继续。

### 变更

- 追问话术按原因分两套：`missing_terminology`（措辞对不上）提示换成本领域说法，
  `unknown_foreign_terms`（外文词一个都不认识）先说明这份资料不覆盖，再把人引回现场设备问题。
  话术仍由服务端给出，模型一次都不调用。
- `failed` 的语义收窄到「模型检索过但一条依据都没有」这一种情形。

### 为什么改

判据是词汇层面的近似，口语化的真问题同样会撞上它（10 条改写问法拦了 7 条）。
误拦本身消除不掉——词面与语义两个维度都试不出干净的分界线——但可以把代价换个形态：
从「答不出」变成「多问一句」。这是产品取舍，不是精度提升。

### 实测

- `Python 怎么装环境`（外文词判据）与 `柜子里一直嗡嗡响，是不是坏了`（措辞判据）都得到
  `needs_clarification` 且模型零调用；库内问题（`MPPT 怎么跟踪组串电压`）判定为 `None`，照常作答。
- 测试 154 → 156 passed。

## [1.0.0] - 2026-09-16

首个完整版本：检索、拒答、语义向量、缓存与限流四条链路都跑通，测试 154 条。

### 新增

- Redis 检索缓存。缓存的是「命中哪些片段、各得多少分」而不是完整检索结果对象；键包含归一化查询、
  `top_k`、语料、是否含受限内容、分数阈值与向量后端指纹，少带任何一项都会让「改了配置却读到旧结果」；
  失效用语料代次而不是删键；空结果同样缓存，因为跑题判定的代价比算分更高。
- 按用户固定窗口限流（`RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS`）。判定放在 `/chat` 最前面，
  被拒请求不建会话、不写消息、不调模型。
- 熔断降级 `ResilientStore`：存储失败一次即打开熔断（`CACHE_FAILURE_COOLDOWN_SECONDS`，默认 30 秒），
  冷却期内直接走进程内实现。没配 `REDIS_URL` 时本来就是进程内实现，本地开发与 CI 不需要 Redis。
- 命令行入口 `smartpv-agent`（`version` / `doctor` / `serve`）与等价的 `python -m support_agent`。
  `doctor` 逐项检查配置、数据库与语料规模、向量后端、模型、检索与拒答参数、缓存与限流，并给出退出码。
- `CHANGELOG.md`、`LICENSE`，以及完整的打包元数据（分类器、关键词、项目链接）。

### 变更

- 版本号收敛为单一来源 `support_agent.__version__`：打包、OpenAPI 文档、`/health` 与 CLI 同源，
  不再出现多处手写导致的版本分叉。
- 包名 `enterprise-support-agent` 改为 `smartpv-support-agent`，与现在的业务域一致。
- 客户端页面目录改为按「`SMARTPV_STATIC_DIR` → 仓库布局 → 当前工作目录 → 包内」依次查找：
  以前写死的是仓库相对路径，装成 wheel 或在容器里跑时 `GET /` 会静默消失。
- `/health` 增加 `version` 与 `cache.{backend,primary,degraded}`。

### 实测

- 库内问题 385ms → 11ms（第二次起命中缓存，全语料词频统计从 1 次降到 0 次）；
  跑题问题 340ms → 0.3ms（命中后连查询向量都不必算）。
- `REDIS_URL` 指向不可达地址：首次操作吃满 1008ms 连接超时，之后 0.0ms，熔断按预期生效。
- 限额设为 3 时：第 1 次 1.83s（含模型加载与那次超时），第 2/3 次 0.04s / 0.03s，第 4 次 429 仅 0.006s，
  且会话数是 3 而不是 4——被拒请求没有留下会话。
- 测试 119 → 154 passed。

## [0.4.0] - 2026-09-16

### 新增

- 可切换的向量后端 `services/semantic.py`：`hash`（384 维，零依赖兜底）与
  `onnx`（512 维，bge-small-zh-v1.5，本地 CPU 推理）。模型用 `[CLS]` 池化，实测比平均池化分得更开。
- 配置项 `EMBEDDING_BACKEND` / `EMBEDDING_MODEL_PATH` / `EMBEDDING_DIMENSION`；
  模型依赖走可选 extra `pip install -e ".[semantic]"`，不进核心依赖、不进镜像、不进仓库。
- `scripts/ingest_smartpv.py --reset`：用 `drop_all` 重建表，而不是删库文件（Windows 上库文件常被运行中的服务占着）。
- `/health` 报出当前生效的 `embedding_backend` 与 `embedding_dimension`。

### 变更

- 片段元数据记录建库用的后端指纹（`hash:384` / `onnx:512`）；检索时若向量长度与当前后端不一致，
  语义那一路记 0 并打警告——余弦相似度在长度不等时会按短向量截断，静默给出一个看着正常的假分数。
- `SEMANTIC_TRUST_FLOOR` 由后端自己声明（哈希 0.4、语义 1.0）：那个折扣本来是给字符碰撞设的，
  真模型不需要。
- 配置写了 `onnx` 但依赖或模型缺失时直接报错，不静默退回哈希向量——两者是不同的向量空间，混用会让检索结果无法解释。

### 实测

- 212 片段语料：库内 top1 最低分 0.226 → 0.324，中位 0.518 → 0.581；领域术语命中率 top-1 83% → 87%。
- 分数阈值这条路被彻底排除：换真语义向量后库外 top1 最高分从 0.353 涨到 0.486
  （「怎么考驾照」被判为与「危险品运输管理」一节相关，那节确实提到驾驶证），分布重叠得更厉害。
- 测试 106 → 119 passed（1 skipped，需模型文件的用例在本机显式开启）。

## [0.3.0] - 2026-09-16

### 新增

- 语料范围判据 `out_of_corpus()`：① 查询词组在整份语料里的缺失比例达到上限；② 查询里的外文词一个都不认识
  （外文词是完整 token，不受跨词切分污染，判定用「全部不认识」而不是「有词不认识」）。
- 前置拒答：判定移到调用模型之前，命中即 `failed` / `policy`，模型零调用。
- `RAGService.corpus_index()`：检索打分与前置判定共用同一份语料统计。

### 变更

- 判据门槛从「文档数 30」改为「片段数 60」（`CORPUS_MISSING_MIN_CHUNKS`），缺失比例上限从 0.75 收到 0.60。
  按文档数算的门槛让判据整条不生效——20 个文件里有 212 个片段。
- `RETRIEVAL_MIN_SCORE` 明确保持 0.0。库内与库外的分数分布始终重叠，不再试图用阈值拒答。

### 修复

- 库外问题不再返回 `completed` / `model` / 0 引用。此前拒答话术是模型自己写的：它看到跑题问题压根不调检索工具，
  直接凭「我是光伏助手」作答，服务端那句「检索过但没有依据」的兜底永远等不到。

### 实测

- 30 条库内问题全部放行，30 条库外问题全部拦下，无误拦、无漏放。
- 测试 102 → 106 passed。

## [0.2.0] - 2026-09-15

### 新增

- 手写 Agent Loop（`services/agent_loop.py`）：工具白名单、参数校验（未知参数拒绝而非忽略）、
  最多 5 轮、轮数用尽时去掉 `tools` 再问一次强制收敛、写操作返回待确认令牌。
- 真实模型接入：填 `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` 即切换；调用失败明确回「模型服务暂时不可用」，
  绝不偷偷退回规则模型；思考模式模型会回传历史 `reasoning_content`。
- `RuleBasedLocalModel`：没有密钥时驱动同一个 Agent Loop，离线演示与自动化测试都用它。
- `/chat` 终态协议：`status`（`completed` / `degraded` / `pending_confirmation` / `blocked` / `failed`）、
  `answer_source` 与 `retryable`。终态一律由服务端判定，模型无权声明。
- 三层离线评测：检索 / 工具选择 / 最终回答，声明哪层就评哪层，没声明的不进分母；
  判定复用线上代码，不另写一份。
- 行业元数据（机型 / 协议 / 文档版本 / 场景 / 片段类型）与跨版本并列提示 `conflicts`。
- 查询侧停用词剥离 + 二元字组打分：`0.30 × 单字 + 0.35 × 二元字组 + 0.35 × 向量`。
- 提示词注入在进入 Agent Loop 之前拦截，模型一次都不调用。

### 变更

- 业务域从电商客服换成光伏电站技术支持：`Order` / `query_order` 改为 `Device` / `query_device`，
  工单的 `order_id` 列改名 `device_sn`。
- `respond` 拆出 `run_turn`（跑完整链路、零落库副作用），`/chat` 在它之上加建会话、写消息、签令牌与写审计。

### 实测

- 8 个口语化问法全部进入 top-5；改前「合母和控母有什么区别」排第 9、「这两个母线有啥区别」排第 23。
- 行业标签从「只扫标题命中 4 个机型 / 0 个协议」改为「扫正文命中 43 个机型 / 18 个协议」。

### 移除

- 示例语料 `sample_data/`、评测集 `evals/dataset.jsonl`、`scripts/seed.py` 迁出仓库。
  语料随资料更新而变，也可能含内部材料，评测集同理，改由调用方在运行时指定路径。

## [0.1.0] - 2026-09-07

### 新增

- 初始骨架：FastAPI + SQLAlchemy（SQLite 演示模式 / PostgreSQL + pgvector）、会话与消息、
  审计日志、文档导入（TXT / Markdown / PDF，按 SHA-256 去重）、知识库检索、LangGraph 规则分类、
  Docker Compose 与 CI 工作流。

[1.3.2]: https://github.com/Garcia-rgb/smartpv-support-agent/compare/v1.3.1...v1.3.2
[1.3.1]: https://github.com/Garcia-rgb/smartpv-support-agent/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/Garcia-rgb/smartpv-support-agent/compare/v1.2.1...v1.3.0
[1.2.1]: https://github.com/Garcia-rgb/smartpv-support-agent/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/Garcia-rgb/smartpv-support-agent/compare/v1.1.3...v1.2.0
[1.1.3]: https://github.com/Garcia-rgb/smartpv-support-agent/compare/v1.1.2...v1.1.3
[1.1.2]: https://github.com/Garcia-rgb/smartpv-support-agent/compare/v1.1.1...v1.1.2
[1.1.1]: https://github.com/Garcia-rgb/smartpv-support-agent/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/Garcia-rgb/smartpv-support-agent/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/Garcia-rgb/smartpv-support-agent/releases/tag/v1.0.0
[0.4.0]: https://github.com/Garcia-rgb/smartpv-support-agent/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Garcia-rgb/smartpv-support-agent/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Garcia-rgb/smartpv-support-agent/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Garcia-rgb/smartpv-support-agent/releases/tag/v0.1.0
