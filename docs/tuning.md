# 参数管理与实验

## 从哪里改

日常调参只修改 `config/agent.toml`，目前集中管理 57 项参数。每项有类型和范围校验，关键参数注明单位。例如重叠长度必须小于切块长度，三路检索权重之和必须为 1。

| 分组 | 内容 |
| --- | --- |
| chunking | 切块字符数、相邻切块重叠字符数 |
| retrieval | 返回及候选数量、分数阈值、词面/词组/向量权重、型号与品牌加减分 |
| evidence | 证据数量、每文档配额、相关性门槛、资料字符预算 |
| history | 历史消息数量、单条消息长度、连续追问描述长度 |
| embedding | 向量编码器批量大小、token 截断长度 |
| generation | 工具轮数、temperature、本地及远程输出 token 上限 |
| versions | 多版本提示的分数范围、版本数量、引用摘要长度 |
| quiz | 题目与选项检索数量、选项领先分差、模型证据数量 |

`.env` 继续保存密钥、接口地址、模型路径、数据库连接和运行安全配置。提示词及业务规则仍在代码中，需按业务含义单独评测。

## 单位与生效

- `chat_history_limit` 按**消息条数**计，不是对话轮数；系统还会保留当前主题首条用户描述，可能额外多一条。
- `embedding_max_length` 是**向量编码器**的 token 截断长度，不是 DeepSeek 上下文窗口，不应超过编码器支持范围。
- 历史和资料预算按**字符数**计，不等于整个请求 token 数；系统提示、标签、题干与工具协议仍占上下文。
- `llm_max_output_tokens=0` 不额外指定远程输出上限，保持现有行为；正整数以 `max_tokens` 发送，需符合提供方支持范围。
- 修改后**重启客户端/服务**。检索缓存和向量后端实例键已区分参数配置。
- 改切块、重叠或向量截断长度不会自动重写资料；需备份后重建索引才能比较效果，重复导入会按校验和跳过原文件。现有 `--reset` 会清空数据库表，**含账号和聊天数据**，不可直接用于日常运行库。对照实验使用 `chunking_experiment.py` 的独立实验库。
- 尚无单独的命中片段前后邻居扩展；`chunk_overlap` 指入库时相邻切块的重叠。

## 优先级与迁移

从高到低：代码/CLI 显式参数 → 进程环境变量 → 实验 TOML → 旧 `.env` 调参项 → 正常 TOML。

正常 TOML 以程序发布的基线补齐缺省项，再叠加 `AGENT_CONFIG_FILE`；未指定时读取当前数据目录的 `config/agent.toml`。升级新增参数后，旧的自定义文件无需一次性补齐全部字段。

旧 `.env` 调参项仍兼容，若未迁移会覆盖正常 TOML。执行一次：

```powershell
python scripts/manage_tuning.py --migrate-env
```

迁移仅处理算法白名单，先备份到 Git 忽略的 `.desktop/tuning-migration-*`，再检查前后有效值一致；失败自动恢复。备份含原 `.env`，保留在本机。启用实验或存在进程调参覆盖时会拒绝迁移，避免误存实验值。

## 查看生效值

```powershell
python scripts/manage_tuning.py
python scripts/manage_tuning.py --output 本地文档/参数快照/baseline.json
python scripts/manage_tuning.py --experiment config/experiments/longer_context.toml
```

输出包含参数白名单、向量后端与维度、配置路径、参数 SHA-256 指纹；不包含密钥、资料正文和聊天内容。

## 对照实验

`config/experiments/` 提供“小切块”和“更长上下文”两份**候选配置**，尚未证明优于基线。实验文件只需写覆盖项：

```toml
[evidence]
evidence_total_chars = 9000
evidence_snippet_chars = 2200
```

先用合成资料、临时数据库做离线验收，不调用外部模型，不改实际资料库：

```powershell
python scripts/run_acceptance.py --output 本地文档/验收报告/baseline.json
python scripts/run_acceptance.py --experiment config/experiments/longer_context.toml --output 本地文档/验收报告/longer-context.json
```

验收、检索评估和切块实验报告自动记录有效参数、指纹与案例文件校验和。合成验收通过只说明功能没有回归，不能证明实际回答质量更好。

真实调参应固定一批有参考答案的问题，每轮只改一类参数，比较检索命中、依据适用性、答案正确性、耗时与 token 用量，并保留独立验证问题。远程生成验收仍需显式 `--remote --allow-external-data`，且满足实际外发授权。

历史 [切块实验记录](chunking_experiment.md) 只比较过一组 HCSA 样本上的三个切块组合。当前配置是可追溯的运行基线，**不是已经求得的全局最优解**。

## 安装与部署

Windows 安装版可编辑文件为 `data/config/agent.toml`，升级保留该文件，新增参数由新版基线补齐。源码、Windows 离线包、wheel 和 Docker 均携带基线。

长期使用实验配置可在本机 `.env` 指定 `AGENT_EXPERIMENT_FILE` 的实际路径后重启；取消即恢复正常配置。真实问题与资料片段的报告仅保存在本地。
