# MCP 只读工具与策略门控控制入口

## 工具清单

| 工具 | 输入 | 返回内容 |
| --- | --- | --- |
| `esp32_workspace_list` | `root`：绝对目录 | 目录本身及直接子目录中识别到的工作区、机型、入口和 Python 文件名；`skippedCandidates` 统计因不可访问而跳过的候选 |
| `esp32_workspace_info` | `path`：绝对目录 | 单个工作区的识别结果、机型、标签、入口和根目录 Python 文件名 |
| `esp32_workspace_current` | 无 | 读取 broker 当前共享工作区，不执行认领/切换 |
| `esp32_open_panel` | `ui`（可选，默认 `web`；`tk` 为回退） | 请求打开同用户单实例 Windows 独立面板；先读取 broker 当前共享工作区和机型，再用相同上下文启动；与 MCP 共用真实桥和 broker，不会自动连接物理串口。隔离测试服务拒绝弹窗 |
| `esp32_serial_ports` | 无 | 按桥当前 profile 列出的串口 |
| `esp32_status` | 无 | 桥的连接、端口、busy、板信息和桥工作区 |
| `esp32_console_read` | 可选 `since`、`max_chars` | 桥已缓冲的控制台事件、cursor 和 dropped 标志 |
| `esp32_board_files_list` | 无 | 模拟模式返回 canned 文件列表；真实访问需 `--enable-write-tools`，并可能影响正在运行的程序 |
| `esp32_board_file_read` | `path`：以 `/` 开头的板载绝对路径 | 模拟模式返回 canned 文件内容；真实访问需 `--enable-write-tools`，旧桥会中断运行程序 |

工作区扫描遵循 DSH 1.2.1 的机型规则：`esp32-ide/board.json` 优先，其次识别幻尔文件名、四足机器人签名文件和有限的文件内容签名。扫描根目录和直接子目录；每个候选最多递归 3 层、每层最多查看 512 项、最多记录 48 个 Python 文件；不跟随符号链接。不可访问的候选会跳过并计入 `skippedCandidates`，其余可读结果照常返回。工具只读，不提供手动认领或 `board.json` 写入。

## 阶段 3a 显式控制工具

默认启动时不注册以下六个设备控制工具。只有配置 `--enable-control-tools` 才能发现它们：

| 工具 | 输入 | 行为与限制 |
| --- | --- | --- |
| `esp32_connect` | 可选 `port` | 检查桥状态与端口列表后连接所选端口；已连接同一端口时返回 `alreadyConnected`，不重复连接 |
| `esp32_disconnect` | 无 | 断开本 MCP 桥的当前连接；不影响 DSH 或其他进程 |
| `esp32_stop` | 无 | 执行机型对应的旧桥停止命令；generic 仅中断程序，所有机型均返回 `physicalStopConfirmed: false` |
| `esp32_run` | 无 | 软重启并运行工作区入口程序 |
| `esp32_interrupt` | 无 | 向板上程序发送中断；不确认电机或舵机已安全停下 |
| `esp32_repl_send` | `line`：最多 512 字符的单行文本 | 发送可执行的 MicroPython 行；按 `write` 操作执行策略确认，不能当作安全只读命令 |

在 `--mode mock --mock-scenario control` 下，工具只更新无硬件模拟状态或记录 canned 响应。`--mode bridge` 下，只有同时明确设置 `--enable-control-tools` 才向真实桥放行相同命令；没有该 flag 时真实桥白名单仍为 `ports`、`status`。显式开启工具不等于绕过工作区策略：`confirm-all` 的 connect/run/interrupt 和 `confirm-write`/`confirm-all` 的 REPL 仍须先处理确认。用户明确授权了目标和效果范围并要求 Agent 操作面板时，Agent 可通过下面的专用决策工具代办精确待办；否则需要真人确认的真实操作在真人渠道验收前 fail-closed。拒绝、取消、不支持或确认错误都不会发出相应命令。`disconnect` 与 `stop` 是释放/紧急动作，不要求额外确认，但结果不会宣称设备已安全停转。模型提供的 `confirmed`、token 或 nonce 不作真人批准依据。

## 面板待办与 Agent 代办

| 工具 | 输入 | 行为 |
|---|---|---|
| `esp32_confirmation_status` | 无 | 仅列出当前 MCP broker lease 发起且仍待决的确认摘要，不含 REPL 正文 |
| `esp32_confirmation_cancel` | `confirmation_id` | 取消当前 lease 发起的待办；不执行原操作 |
| `esp32_panel_agent_decide` | `confirmation_id`、`decision` (`approve`/`reject`) | 对当前 lease 自己发起的待办作出 Agent 决策；批准后原调用继续并消费同一确认一次 |

面板真人按钮决策记录为 `human_panel`；`esp32_panel_agent_decide` 记录为 `agent_delegated`。Agent 只在用户明确授权的目标/效果完整覆盖待办时批准；代办不是真人点击，也不算真人确认渠道验收。broker 按创建 lease、请求上下文、workspace/profile/entry、epoch、策略 revision 和 TTL 约束操作；错误 lease、错 ID、拒绝、过期、上下文变化或重复消费不执行设备命令。原调用等待期间可以用状态和决策工具继续处理；不要因等待超时而重放控制或 REPL 请求。

## 阶段 3b 写入与板载文件工具

以下新增工具只有显式 `--enable-write-tools` 才注册。真实桥模式还必须显式设置 `--enable-control-tools`；这两个开关分开，避免升级后原有控制配置意外注册写删功能。模拟模式使用 `--mock-scenario fileops`，结果标记为模拟数据。

| 工具 | 输入 | 行为与限制 |
| --- | --- | --- |
| `esp32_download` | 工作区根目录 `.py` 文件名、可选 `run` | 写入检测到的入口；拒绝子路径、符号链接、超限及非 UTF-8 源文件。授权摘要先安全读取并列出 SHA-256，授权后重读并比对摘要；源文件变化时在发桥命令前拒绝。覆盖前严格备份，下载后校验 size/CRC32/语法；仅校验成功才可按 `run` 重启 |
| `esp32_board_files_list` | 无 | 列出板载根目录。旧桥通过 REPL 查询，可能受程序运行状态影响 |
| `esp32_board_file_read` | 板载绝对路径 | 读取板载 UTF-8 文本；旧桥会中断运行程序并执行机型停止动作，结果说明这一副作用 |
| `esp32_board_file_write` | 板载绝对路径、UTF-8 文本 | 覆盖现存文件前严格备份；仅可靠确认目标不存在时可新建 |
| `esp32_board_file_delete` | 板载绝对路径 | 删除前严格备份，保存备份可回退；任何备份失败时不发送删除命令 |
| `esp32_pid` | `get` 或 `set`，可选 Kp/Ki/Kd 数值与 `run` | 使用工作区配置入口（hiwonder `/corex.py`；generic 与四足 `/main.py`）；set 严格备份，`run` 仅在写入校验成功后执行 |

严格备份为 bridge 新增的可选 Codex 路径：MCP 在任何 mutation 前查询 `capabilities`，不支持时不发送 download/writefile/deletefile。PID 还要求原始读取 capability。请求传 `strictBackup: true` 后，由同一 bridge 命令内执行目标存在性核验、原始 bytes 读取、本地备份独占创建、flush/fsync、回读与长度/CRC 比对，全部通过才改动目标。响应中的 `targetExisted`、`backupVerified`、`backupPath`、`backupSize`、`backupCrc` 明确表达状态；MCP 不根据旧 `backup` 字段推断成功。旧 DSH 调用不传 strict 参数，原行为保持不变。

策略按操作副作用分类：board list/read 是 `control`，在 `confirm-all` 下需逐次处理；下载、board write/delete 和 PID set 是 `write`，在 `confirm-write` 与 `confirm-all` 下需逐次处理。用户明确授权范围内可走上述 Agent 代办；实际需要真人决定的真实操作在真人确认渠道验收前 fail-closed。严格备份门槛不受 `auto` 策略影响，始终执行。

## 阶段 3a mock-only 控制/策略测试入口

| 工具 | 输入 | 模拟行为 |
| --- | --- | --- |
| `esp32_mock_policy_get` | 无 | 读取当前工作区三档策略文件；`source` 为 `default` 或 `workbench-policy.json`，`dataSource: local_workspace`，`simulated: false`；不访问设备 |
| `esp32_mock_connect` | 可选 `port`，夹具仅接受 `MOCK0` | 改变模拟器连接状态，不打开串口 |
| `esp32_mock_disconnect` | 无 | 清除模拟连接状态，不操作设备 |
| `esp32_mock_stop` | 无 | 返回旧桥 `stopped` 响应形状；明确标记没有物理停转确认 |
| `esp32_mock_run` | 无 | 记录模拟请求，不运行入口文件 |
| `esp32_mock_interrupt` | 无 | 返回中断响应形状，不向设备发送 Ctrl-C |
| `esp32_mock_repl_send` | `line`：最多 512 字符的单行文本 | 只记录行文本，不执行代码；拒绝空行和控制字符 |

这些 MCP 工具必须与 `--mode mock --mock-scenario control` 配合。默认策略为 `confirm-write`；`confirm-all` 会对 connect/run/interrupt 请求逐次 MCP elicitation，`confirm-write` 与 `confirm-all` 会对模拟 REPL 请求逐次 elicitation。stop/disconnect 为紧急/释放动作，不弹出确认。`--allow-mock-elicitation` 只为测试客户端授权分支启用 elicitation，不验证 Codex UI 的人类确认能力。拒绝、取消、客户端不支持或异常都阻止模拟动作；不得通过 `confirmed`、token 或 nonce 代替确认。

`esp32_mock_*` 工具名称明确表示模拟操作。若服务以 `--mode bridge` 启动，这些工具会先拒绝调用，不启动真实 bridge 子进程。generic 的 stop/interrupt 结果不能证明电机或舵机已安全停下。

板载路径拒绝空段、`.`、`..`、反斜杠、引号、控制字符和超过 240 字符的输入。阶段 2 的只读边界曾拒绝真实 list/read；阶段 3b 在显式写工具开关下开放，并把 `readfile` 可能中断运行程序及执行机型停止动作纳入返回和策略影响说明。

## 模拟和真实桥来源

直接运行 `mcp-server/server.py` 默认使用 `--mode mock`，启动无硬件夹具 `tests/mock_bridge.py --scenario readonly`。个人插件安装和 `.mcp.json` 使用包内 `launch_bridge.cmd`，显式设为 `--mode bridge`，注册控制与写入工具并安装 `pyserial==3.5`。真实结果包含 `source: "bridge_process"`、`simulated: false`。启动只枚举串口和读取状态，不自动连接；用户也可从开始菜单打开真实紧凑网页面板。`launch_mock.cmd` 与 MOCK Web/Tk 快捷方式保留作开发回退。MOCK0、板信息、文件和控制台内容都是合成数据；`--mock-scenario busy` 是固定访问拒绝样本，只验证 DSH 手动断开的错误提示。

显式设置 `--mode bridge` 才可能启动用户通过绝对路径指定的 `bridge.py`。桥数据包含 `source: "bridge_process"`、`simulated: false`。未设置 `--enable-control-tools` 时真实桥仅允许 `ports`、`status`；该 flag 才将 connect/disconnect/stop/run/interrupt/send 加入客户端白名单。写入工具还要求 `--enable-write-tools`； capability 不支持时，严格下载、覆盖、删除和 PID 修改在任何 mutation 命令前拒绝。`esp32_mock_*` 工具在真实模式下仍会拒绝并且不会启动桥进程。模拟结果不会伪装成真实设备读数。`requirements.txt` 固定 MCP Python SDK；`requirements-bridge.txt` 另固定旧桥需要的 pyserial。没有依赖 DSH 安装目录中的 SDK，也没有把 `mcp` 或串口库放进模拟桥。

若安全的状态调用报 `Access is denied` 或资源占用，工具会提示可能由 DSH 使用串口，并要求用户在 DSH 内手动断开；服务不会结束其他进程或尝试自动抢占。端口枚举本身不判断哪个进程持有串口。阶段 2 未实现连接，所以不能据此报告已确认 DSH 占用。

## 安装与无硬件验证

在项目根目录使用 Python 3.10 或更新版本。官方 OpenAI MCP 示例使用 `mcp==1.26.0` 和 `FastMCP` stdio；本项目固定相同 SDK 版本，并由 SDK `ClientSession` 测试只读工具与 mock-only 控制入口。

```powershell
$python = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
& $python -m pip install -r requirements.txt
& $python -B -m unittest discover -s tests -p "test_readonly_mcp.py" -v
```

这些命令只启动本地无硬件模拟桥。阶段 3a 模拟控制策略分支测试命令是：

```powershell
& $python -B -m unittest discover -s tests -p "test_mock_control_mcp.py" -v
& $python -B -m unittest discover -s tests -p "test_control_tools_mcp.py" -v
```

后一个测试用临时 canned JSON-lines bridge 检查显式工具注册、真实命令默认关闭、策略门禁与控制响应；不会启动 `../bridge.py`，也不会访问串口或设备。测试由独立 tester 运行；本轮开发 agent 不执行。MCP elicitation 的测试 callback 是合成客户端，只验证 accepted/declined/unsupported 协议分支，不验证 Codex UI 人类确认。若要单独跑阶段 1 的纯标准库协议测试，不安装 MCP SDK：

```powershell
& $python -B -m unittest discover -s tests -p "test_protocol_simulator.py" -v
```

如运行环境未预装 `mcp==1.26.0`，首次安装需要本机 Python 包源可用；模拟桥本身不需要网络或第三方库。真实桥模式另外需要：

```powershell
& $python -m pip install -r requirements-bridge.txt
```

## Codex 本地配置示例

阶段 2 只提供配置范例，不修改用户级 Codex 配置，也不安装插件。将尖括号内容替换为本机的**绝对路径**，可先以模拟模式接入：

```toml
[mcp_servers.esp32_readonly]
command = "<Python 解释器绝对路径>"
args = [
  "-X", "utf8",
  "<项目绝对路径>\\mcp-server\\server.py",
  "--mode", "mock",
  "--workspace", "<工作区绝对路径>",
]
cwd = "<项目绝对路径>"
```

Codex CLI 和 IDE 共用 MCP 配置；Codex 本地 stdio 服务使用 `command`、`args`、`cwd` 字段。真实桥配置须显式改用 `--mode bridge` 并补充 `--bridge-script <bridge.py绝对路径>`，还需按 `requirements-bridge.txt` 安装 pyserial。阶段 2 不对真实桥做运行验证。配置字段参见 [Codex 配置参考](https://developers.openai.com/codex/config-reference/)；`FastMCP` 与 SDK 版本参见 [OpenAI MCP 连接示例](https://developers.openai.com/api/docs/guides/agents-api/tools/mcp)。

## 当前面板步骤 4 内部共享路由

## 当前面板步骤 5 工作区同步

新增只读 MCP 工具 esp32_workspace_current，返回 broker 当前工作区、机型、入口、共享 epoch，且不启动真实桥/连接串口。工作区认领、显式切换与策略版本比对写入是内部 RPC，要求单租约、断开与 epoch 不变；当前不注册为模型可直接调用的变更工具。后续由面板逐次取得人工确认才提供相应入口。

已实现的 MCP 文件工具名称、入参及响应合同保持不变；底层由同一命名管道 broker 持有唯一 BridgeClient，`file` RPC 白名单执行 capabilities/listfiles/readfile/download/writefile/deletefile，PID get/set 分别复用 raw read 和 strict write。broker 再次校验真实文件开关、路径、严格备份、capability 和共享 epoch，所有文件、控制与状态在同一后端串行；旧文件工具原本在 MCP 本地桥执行的过渡实现已撤销。分帧机制保留每帧 1 MiB 限制，1 MiB 源文件和 Base64 响应使用最多 2 MiB 逻辑消息及 SHA-256 重组。真实人类确认通道与设备行为尚未通过联调；需确认的真实动作继续 fail-closed。
