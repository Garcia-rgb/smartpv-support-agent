# 光伏电站技术支持 Agent

[![version](https://img.shields.io/badge/version-1.10.1-blue)](CHANGELOG.md)
[![python](https://img.shields.io/badge/python-3.11%2B-3776ab)](pyproject.toml)
[![license](https://img.shields.io/badge/license-MIT-yellow)](LICENSE)

面向光伏服务人员的技术支持工具，集中处理 **现场问题排查、课程刷题讲解和点表制作**。
支持文字输入、上传或粘贴截图，并提供 admin / user 账号、资料管理和本机备份。

当前版本 **v1.10.1**，适合在 Windows 本机试用和逐步补充业务资料。企业运行框架已搭建，回答质量与协议解析仍需结合实际资料和现场结果验收。

## 主要功能

| 业务 | 当前支持 |
|---|---|
| 现场问题 | 从本机资料库检索依据，配置远程模型后整理回答；显示资料引用，支持追问和反馈 |
| 学习刷题 | 识别题干及选项，结合题库、检索资料和模型给出选项与解释；依据不足时提示核对 |
| 点表制作 | 选择南向或北向，上传设备协议 Word / 文字版 PDF，解析候选点位、手动补充字段并导出 CSV |
| 工单 | 在项目内创建和查看工单，写入前人工确认；尚未对接企业实际工单系统 |
| 账号权限 | admin 管理账号与资料；user 使用业务功能、查看自己的会话和工单、修改密码 |
| 管理中心 | 运行概况、资料版本及分库管理、启用/停用、操作记录、SQLite 备份下载 |

### 文字和图片使用同一个入口

输入文字，点击「上传图片」，或直接在输入框粘贴截图。图片先在本机 OCR 成文字，再按内容判断：

- 有明确题目和选项结构的内容进入刷题讲解。
- 客户聊天、告警和现场描述进入技术问答。
- 识别文字可核对、修改后重新提交。

当前图片处理以文字识别为主，不能凭纯设备照片判断故障。分类和识别可能出错，需要核对识别结果。

### 点表需要现场核对

协议解析在本机进行，协议文件不送给 DeepSeek。支持 `.docx`、文字版 PDF 和部分旧 `.doc` 文件；扫描件需先做 OCR。不同协议版式可能需要人工补点。

生成前可修改点位及现场字段。南向地址与北向平台点号分别填写；地址基数、字序、倍率、控制值和设备信息需按现场协议核实。输出为 CSV，当前不在服务端保存生成文件。

## 数据与模型如何处理

**本机运行不等于所有数据都留在本机。** 数据库、资料原件、检索和 OCR 在本机；启用远程模型后，部分问答内容会发送到配置的模型服务。

| 内容 | 处理方式 |
|---|---|
| 账号、会话、工单、反馈 | 保存到配置的数据库；本机默认 SQLite |
| 导入资料 | 在本机切分、生成向量并检索；新上传原件保存到 `knowledge_base/` |
| 普通 / 隐私资料库 | 用于分类和控制检索范围；目前两类问答都可调用 DeepSeek，分库本身不阻止外发 |
| 分流后的资料问答 | 将本次问题和命中片段交给远程模型整理，该路径不附带聊天历史 |
| 工具调用等其他模型路径 | 使用独立的 Agent 流程，可能携带近期会话上下文；不能把所有路径都视为无历史外发 |
| 题目截图 | 先本机 OCR，模型兜底可能发送识别出的题目、选项和资料片段 |
| 公开网页搜索 | 需另外配置搜索服务，只发送白名单产品和通用技术词；DeepSeek API 本身不自动联网搜索 |

新克隆项目的 `.env.example` **默认关闭远程模型**；未配置时使用本地规则和检索摘要，生成能力有限。启用远程服务前应确定资料使用范围。关闭远程调用可设置 `ALLOW_REMOTE_LLM=false`。

真实资料、模型密钥、数据库、日志和备份不随仓库分发。详细说明见 [资料分流说明](docs/privacy_routing.md)。

## Windows 本机快速开始

需要 Python 3.11 或更高版本。在项目目录中运行以下步骤；已有环境和 admin 账号可跳过对应初始化步骤。

### 1. 下载和安装

```powershell
git clone https://github.com/Garcia-rgb/smartpv-support-agent.git
cd smartpv-support-agent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[quiz]"
Copy-Item .env.example .env
```

`quiz` 安装本机图片识别所需依赖。首次使用 OCR 时可能需要下载模型文件，请预留网络和磁盘空间。

### 2. 配置模型（可选）

编辑 `.env`，远程问答需要同时配置：

```dotenv
ALLOW_REMOTE_LLM=true
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=<你的账号可用的模型名称>
LLM_API_KEY=<你的 API Key>
PRIVACY_ROUTING_ENABLED=true
```

模型名称以服务商及账号实际可用配置为准。不要将真实 Key 写入代码、README 或提交到 Git。

### 3. 创建管理员并启动

```powershell
.\.venv\Scripts\python.exe scripts/create_admin.py --generate
.\.venv\Scripts\python.exe scripts/start_client.py
```

记下首次显示的 admin 密码，登录后修改密码。已有 admin 时，创建脚本不会覆盖账号或密码。

默认地址为 **http://127.0.0.1:8000/**；端口被占用时，启动器会尝试其他端口，以窗口显示的地址为准。启动窗口需要保持运行；电脑关机或休眠时服务不可用。

### 4. 添加资料和用户

- admin 登录后，在「管理中心」导入资料，核对所属库、资料版本和启用状态。
- 在「管理用户」创建 user 账号，将一次性初始密码交给对应人员。
- 仓库不包含业务资料，需自行导入后才能验证检索和答题效果。

需要桌面启动入口时运行：

```powershell
.\.venv\Scripts\python.exe scripts/create_desktop_launcher.py
```

生成的 `.cmd` 绑定当前项目路径和 Python 环境；移动项目或更换环境后需要重新生成。当前启动器默认仅本机访问，不会自动向局域网开放。

## 技术栈

| 层次 | 技术 |
|---|---|
| 网页 | HTML / CSS / 原生 JavaScript，目前未使用 Vue 或 React |
| 后端 | Python、FastAPI、Pydantic、Uvicorn |
| 数据存储 | SQLAlchemy；本机 SQLite，可选 PostgreSQL / pgvector |
| 检索 | 字符及关键词打分、向量检索、场景元数据与资料版本提示 |
| 本机推理 | 可选 ONNX Runtime 语义向量；RapidOCR 图片文字识别 |
| 问答与流程 | OpenAI 兼容模型接口、工具调用、LangGraph 状态流程、人工确认 |
| 运行保障 | 服务端会话和角色权限、审计、轮转日志、缓存与限流；可选 Redis |
| 部署与验证 | Docker Compose、pytest、Ruff、离线评测工具 |

默认 `hash` 向量用于轻量试用，不具备语义模型的理解能力。启用 ONNX 需安装 `semantic` 可选依赖并另行准备模型文件；切换向量后端后需重新构建对应语料向量，操作前先备份。

## 管理、备份与部署

本机默认持久化位置：

- `support_agent.db`：账号、会话、资料片段等数据库内容。
- `knowledge_base/`：资料原件及本机业务资料。
- `.backups/`：管理员创建的备份。
- `.logs/`：轮转运行日志。

admin 可在管理中心创建和下载 SQLite 在线快照与资料备份。恢复工具会验证校验值、恢复到新目录并撤销旧登录会话。备份含内部资料和账号密码哈希，应妥善保存。当前没有自动创建定时备份任务。

`/health` 查看服务状态，`/ready` 检查数据库就绪。Docker Compose 提供 PostgreSQL、Redis 和持久化目录配置，正式部署还需落实 HTTPS、独立密钥、备份策略及实际验收。本项目尚未完成企业服务器部署。

操作步骤见 [企业运行框架](docs/enterprise_framework.md) 和 [部署说明](docs/deployment.md)。

## 开发与检查

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,quiz]"
.\.venv\Scripts\smartpv-agent.exe doctor
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check src tests
```

历史性能、覆盖率和评测结果对应各自的测试环境与语料，不作为当前所有场景的准确率或响应时间保证。具体记录见 [更新日志](CHANGELOG.md) 和 [架构说明](docs/architecture.md)。

## 文档导航

- [更新日志](CHANGELOG.md)：版本与变更记录。
- [企业运行框架](docs/enterprise_framework.md)：管理中心、备份恢复与本机运行。
- [后续建设计划](docs/enterprise_build_plan.md)：数据质量、业务验收及部署事项。
- [资料分流说明](docs/privacy_routing.md)：本机处理、模型外发和搜索范围。
- [架构说明](docs/architecture.md)：模块、检索及历史评测设计。
- [部署说明](docs/deployment.md)：运行配置与部署检查。
- [安全说明](SECURITY.md)：安全约束和注意事项。
- [API 示例](docs/api_examples.http)：开发联调请求。
- [MCP 接口](docs/mcp.md)：可选工具接口。

## 使用边界

项目用于辅助技术支持和学习。回答需要结合资料版本、设备型号及实际现场核实；点表需完成对点后再导入生产系统。设备档案和工单能力目前属于项目内的业务框架，未自动连接企业真实系统。

代码采用 [MIT 许可证](LICENSE)。外部协议、课程及公司资料的权利和使用范围由资料提供方确定，不随代码许可证转让。
