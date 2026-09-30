# MCP Server 与 Client

> 代码：`src/support_agent/mcp_server.py`（Server）、`scripts/mcp_client_demo.py`（Client）、
> `tests/test_mcp.py`（跨进程集成测试）。
> 依赖：`mcp` 2.2.0。注意 2.x 把 `FastMCP` 改名成 `MCPServer`，字段也统一成了 snake_case，
> 照 1.x 的文档写会一路撞 `ModuleNotFoundError` 和 `AttributeError`。

## 三个角色

| 角色 | 是什么 | 本项目里的对应物 |
|---|---|---|
| Host | 承载模型的那一端，决定要不要调用工具、要不要让用户确认 | Agent 服务（`services/agent.py`） |
| Client | Host 里负责连某一个 Server 的连接器，一个 Client 对一个 Server | `scripts/mcp_client_demo.py` |
| Server | 提供能力的那一端，工具真正在这里执行 | `src/support_agent/mcp_server.py` |

关键在最后一行：**工具的执行权在 Server 进程里**。Client 通过 stdio 发 JSON-RPC，
两边不共享任何 Python 对象。这和「在本进程里调一个函数」是两回事——正因为如此，
Server 的返回内容必须按不可信输入处理（见文末）。

## Tools、Resources、Prompts 的区别

三者最容易混成一件事，区别在「谁发起」和「有没有副作用」：

| | Tools | Resources | Prompts |
|---|---|---|---|
| 是什么 | 可执行动作 | 只读数据 | 提示模板 |
| 谁发起 | 模型申请，Host 批准 | Host 按 URI 读取 | 用户显式选择 |
| 有副作用吗 | 可能有（本项目只暴露只读工具） | 没有，读多少次都一样 | 没有 |
| 标识方式 | 名字 + 参数 schema | URI（如 `device://catalog`） | 名字 + 参数 |
| 本项目里的例子 | `query_device` | `device://catalog` | `fault_report` |

Prompts 单独列出来的意义在于：**它是给人用的，不该由模型自己偷偷套用**。
本项目把它做成「故障上报模板」——用户想按结构描述问题时选它，而不是模型每轮自动加一段。

## 实测输出

`python scripts/mcp_client_demo.py`：

```text
[握手] 连上 smartpv-device 1.4.0
[Tools] query_device：按设备序列号查询逆变器型号、额定功率、运行状态、固件版本和并网情况。
[调用 query_device] isError=False
  {"sn": "SN-2024-000123", "model": "SUN2000-100KTL-M1", ..., "grid_connected": true}
[调用不存在的设备] isError=True
  Error executing tool query_device: 未找到设备 SN-2024-000999
[Resources] device://catalog：设备清单
[Prompts] fault_report：故障上报模板
```

## 业务失败为什么必须报成 isError

第一版把查不到设备写成了普通返回值（`return "错误：未找到设备…"`），Client 收到
`isError=False`——**协议层看不出这次调用失败了**。Host 想做「工具失败率」这类监控时，
只能去读文本猜「哪段话算错误」，这是把结构化信号降级成了字符串匹配。

现在改成抛 `MCPServer` 的 `ToolError`，SDK 会转成 `isError=True` 并保留可读的错误文本。
两边都要：协议层给 Host 一个确定信号，文本层让模型知道哪儿不对。

这和 `services/agent_loop.py` 里「工具报错是信息不是崩溃」并不矛盾：那条讲的是
**不要让一次工具失败打断整轮对话**（所以不向上抛异常），这条讲的是**失败要有个字段标出来**。
前者管流程，后者管可观测性。

## 为什么 MCP 工具的返回内容仍是不可信输入

因为它跨了进程边界，Host 无法验证 Server 到底拿什么拼出了这段文本：

- Server 可能查的是被污染的数据库，返回内容里夹着「忽略以上指令」这类句子；
- Server 可能根本不是自己人写的——MCP 的设计就是让 Host 连第三方 Server；
- 即使 Server 可信，它读的文档本身也可能是用户上传的。

所以从 MCP 拿回来的文本，处理方式和「用户直接输入」应当一样：**当作数据，不当作指令**。
本项目里 `search_knowledge_base` 返回的片段已经面临同一类问题
（`docs/architecture.md` 里记着「工具结果尚未做长度截断与内容隔离」，这是还没补的一环）。

## 鉴权、用户同意与权限边界分别归谁

按当前 MCP 规范的分工：

| 事项 | 谁负责 | 说明 |
|---|---|---|
| Authorization | Host | 走 OAuth 拿令牌；Server 侧可以用 `auth_server_provider` / `token_verifier` 声明并校验（`MCPServer` 构造参数里留了这两个口子） |
| 用户同意 | Host | 工具调用、资源读取、采样都要让用户看见并能拒绝，Server 不能替用户点头 |
| 工具权限边界 | Server 声明能力，Host 决定放行 | Server 用 `list_tools` 说自己有什么；**每次调用放不放由 Host 判断** |

第三行是本项目已经实践过一条的原则：**工具说明书（发给模型看的 schema）不等于工具白名单
（服务端进程内真正可执行的表）**。模型可以申请一个名字，Host 说不执行就是不执行。
MCP 只是把这条原则从「同一进程内的两个数据结构」扩展成「两个进程之间的协议」。

本项目的 Server 目前**没有配鉴权**，也没有暴露任何写操作——它只提供只读查询，
故意的。要接鉴权时的入口是 `MCPServer` 的 `auth_server_provider` 与 `token_verifier`。
