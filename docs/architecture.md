# 技术架构

## 当前面板扩展（UI-10 已交付）

2026-09-28 更新：步骤 UI-1–UI-10 已完成。当前个人插件 MCP launcher 与主 Web 面板使用真实桥，MCP 工具和窗口通过现有命名管道 broker 共享一个连接；MOCK Web 与 Tk 回退保留。启动器仅枚举/读取状态，不自动连接。页面从可信本地资源加载，不开放 HTTP/WebSocket；宿主只提供有限状态和面板动作入口，服务端继续检查策略、状态及逐次确认。实机验收、CLI 安装与限制见 [当前状态](status.md) 和 [专项计划](web-panel-plan.md)。

现有基础架构如下文。Windows 单实例本机控制进程独占桥；窗口与各任务的 stdio MCP 服务通过本机 Windows 命名管道 JSON 版本协议访问同一状态和有界控制台。协议不开放网络监听；客户端身份、版本、单实例、串行操作、租约及异常资源清理按当前计划实现。窗口启动不自动连接串口。

原有 MCP 工具输入输出合同、三档策略、真实模式显式开关、严格备份和 DSH 独立连接边界保留。窗口只批准自身操作，不能代替 Codex 对话确认。一个 Windows 用户只允许一个活动工作区/设备；连接中工作区冲突必须明确报错。步骤 1–10 已交付；实施记录见 [plan.md](plan.md)。

步骤 1 的 IPC 位于 `mcp-server/broker/`：每个 Windows 用户以 SID 派生命名管道和全局 mutex；命名管道及令牌文件的 DACL 只授权当前 SID。协议版本 2 的 hello 要求严格 schema、随机令牌、空闲期限和原 MCP 桥配置；schema、版本或令牌不符即拒绝。消息为 UTF-8 JSON 对象，以 4 字节网络序长度帧承载，每个物理 JSON 帧上限 1 MiB；步骤 4 对文件载荷使用校验哈希与严格序号的分帧传输，逻辑消息最多 2 MiB，以容纳 1 MiB 原始文件的 Base64 膨胀。Windows 管道只读写 bytes，不使用 pickle。每条已认证管道连接持有一个租约；断管在 finally 中释放租约，最后租约离开时关闭 broker 创建的桥。桥子进程加入 `KILL_ON_JOB_CLOSE` job，broker 异常退出时由 Windows 清理其子进程。

步骤 2 将 MCP `esp32_serial_ports`、`esp32_status` 和 `esp32_console_read` 路由到 broker 的 `ports`、`status`、`console` 只读 RPC。MCP 客户端把现有 mode、workspace、profile、bridge/mock 脚本、scenario 和真实模式开关放入 hello；重叠租约按实际生效的运行配置比较并在冲突时拒绝连接。`mock_scenario` 只影响模拟桥，因此真实 `bridge` 模式忽略它的差异，`mock` 模式仍按场景隔离；最后租约释放、桥关闭后允许新配置接入。console RPC 从 broker 所持 `BridgeClient` 的有界缓冲返回相同 cursor。步骤 3 后，控制 RPC 在 broker 锁内检查配置门禁、连接/端口状态、确认等待期间的共享控制 epoch，再调用桥命令并更新 epoch。

## 当前实现

```text
Codex / MCP 客户端
        │ stdio（官方 Python MCP SDK / FastMCP）
        ▼
mcp-server/server.py ── 只读 MCP 工具、来源标记、显式 mock-only 和条件注册控制工具
        │
        ▼
mcp-server/broker/ ── 共享 ports/status/console/control/file、租约和串行 IPC
        │
        ├── broker 持有的 bridge_client.py ── JSON-lines 请求/响应、共享事件缓冲
        │   ├── 默认：tests/mock_bridge.py（无硬件）
        │   └── 显式配置后：bridge.py → pyserial → ESP32（控制工具另需 enable flag）
        │   （文件操作也由 broker 内唯一 bridge 处理）
        └── mcp-server/workspaces.py → 本地 board.json / Python 特征（只读）
```

MCP server 使用独立声明的 `mcp==1.26.0` 和 FastMCP stdio transport，不依赖 Codex/DSH 安装目录里的 SDK 或私有模块。`requirements-bridge.txt` 另声明 `pyserial==3.5`，仅供显式配置的旧桥模式使用。模拟服务器默认不导入 `bridge.py` 或串口库。

阶段 2 首次只开放模拟板载文件工具，真实桥当时仅允许 `ports`、`status`。阶段 3b 后真实文件 list/read 需显式配置 `--enable-control-tools --enable-write-tools`；readfile 会中断板上程序并执行机型停止动作，所以工具返回明确影响说明，并在 `confirm-all` 下经过服务端确认门禁。Codex UI 人类确认能力尚未实测，需确认的真实操作仍 fail-closed。

阶段 3a 保留显式 `esp32_mock_*` 测试入口，并增加 `esp32_connect`、`esp32_disconnect`、`esp32_stop`、`esp32_run`、`esp32_interrupt`、`esp32_repl_send`。六个通用控制工具只有显式配置 `--enable-control-tools` 时才注册；在 `--mode mock --mock-scenario control` 下连接状态只改变模拟器，REPL 只记录输入。现由 broker 串行运行控制桥命令；broker 再检查真实控制 flag。确认通道未由 Codex UI 验证，因此需确认的真实控制命令发送前 fail-closed。步骤 4 已移除 write-enabled 本地桥过渡例外。

阶段 3b 的 download、board write/delete 和 PID 工具由单独的 `--enable-write-tools` 控制注册；真实桥模式还要求 `--enable-control-tools`。写删命令首先执行新 capability 握手，不支持严格模式的旧桥不会收到 mutation 请求。MCP 传 `strictBackup: true` 后，bridge 通过成功列出父目录确认目标状态；目标存在时从板子读取原始 bytes，在工作区根目录独占创建备份、flush/fsync 并回读校验 bytes/长度/CRC，再执行目标覆盖或删除。PID 使用 raw bytes 读取和 CRC 比对防止覆盖过期内容。旧 DSH 不传 strict 参数，路径行为保持不变。文件 list/read 也需要显式写工具启用；读取可能中断程序，需按 `control` 分类。

步骤 3 将非 write-enabled MCP 服务的 `connect`、`disconnect`、`stop`、`run`、`interrupt`、`send` 作为 `control` RPC 串行送入 broker；broker 再次检查真实模式的 `allow_real_controls`，直接 IPC 请求不能绕过 `--enable-control-tools`。状态响应中的内部控制 epoch 不暴露到 MCP；需确认的操作将准备阶段取得的 epoch 带回执行阶段，若其他客户端已改变控制状态则拒绝执行。共享 broker 桥和控制台用于同一批客户端。为保留步骤 2 已有 fileops 服务行为，启用 `--enable-write-tools` 的 MCP 服务在步骤 4 文件 RPC 迁移前继续由本地桥执行文件、控制和读取；它会持有排他的本地桥租约，broker 不启动第二桥，并拒绝其他配置接入，防止两个 bridge 争用真实串口。窗口尚未接入。

步骤 4：所有 MCP 文件读取、严格下载/写删和 PID 均通过唯一 broker 桥处理；broker 串行执行文件及控制，按共享 epoch 与设备连接状态复核。真实模式再次要求 allow_real_writes；mutation 必须 strictBackup:true 且 bridge 声明 capability。工具仍复核严格备份、长度、CRC、语法及 run 顺序；原 DSH 不传 strict 的默认路径不变。读文件、列目录和 mutation 更新 epoch。大于 1 MiB 的逻辑 JSON 以每块 384 KiB 数据、严格顺序和 SHA-256 的多帧方式运输，单帧不超过 1 MiB。旧长驻 broker 无 file RPC 时不抢占连接，须安全释放后重新启动。

## 数据来源与路径

- MCP 服务代码默认 `--mode mock`，使用无硬件模拟桥；Codex 插件 launcher 显式指定 `fileops` 场景，以便包内工具发现涵盖读、控制和写入工具。所有模拟桥数据附带 `source: "mock_bridge"` 和 `simulated: true`；MOCK0、板信息、控制台与板载文件都是固定夹具数据。控制工具测试可用 `control` 场景；连接状态只改变模拟器内存，run/interrupt/stop 只返回模拟响应，REPL 不执行文本。
- 只有显式设置 `--mode bridge`、`--workspace <绝对路径>` 和 `--bridge-script <绝对路径>` 才可能启动真实 `bridge.py`。桥结果附带 `source: "bridge_process"` 和 `simulated: false`。不加 `--enable-control-tools` 时真实桥命令限于 `ports`、`status`；该 flag 才注册并允许控制工具。写工具还要求 `--enable-write-tools`，两项配置缺一时客户端拒绝文件命令。任何 `esp32_mock_*` 工具仍在创建桥子进程前拒绝。
- 工作区工具要求用户提供绝对路径。列表只检查指定目录及其直接子目录；每个候选的 Python 特征扫描最多 3 层、每层 512 项、48 个 Python 文件，不跟随符号链接，不遍历整个磁盘。路径不是代码常量，不依赖用户名或固定盘符。
- 工作区检测沿用 DSH 1.2.1 的 `board.json`、幻尔文件名、四足机器人签名文件与内容特征。仅报告发现的信息，不写入配置，也不改变活动工作区。
- 桥标准输出只用于 JSON-lines 解析。桥日志不转发给 MCP stdout；`event=console` 放入有界内存环形缓冲，由读取工具返回。

## 串口和连接限制

串口列表与状态查询只做读取。端口枚举不会报告具体进程占用者。真实桥控制工具默认不注册；只有显式配置 `--enable-control-tools` 后才可调用。真实桥板载文件工具还要求 `--enable-write-tools`；`readfile` 可能打断运行程序，`confirm-all` 生产调用在 UI 确认能力验收前仍拒绝。若连接时报访问拒绝或资源占用，工具指出 DSH 可能持有串口，要求用户在 DSH 中手动断开；服务不会查杀、关闭或重置其他进程。无硬件验证使用模拟桥或测试中生成的 canned JSON-lines bridge，不使用 `../bridge.py`。

MCP 服务只会停止自己创建的桥子进程，不会查找或管理其他 Python、DSH 或串口进程。真实桥 disconnect 只作用于 MCP 自己创建并拥有的桥连接。

## 验证与后续交付

`tests/test_readonly_mcp.py`、`tests/test_mock_control_mcp.py` 与 `tests/test_control_tools_mcp.py` 使用固定版本 MCP SDK 的 `ClientSession` 通过 stdio 发现和调用工具。桥模式控制测试只使用测试动态生成的 canned JSON-lines 子进程，绝不启动 `../bridge.py` 或连接串口。MCP elicitation callback 是合成客户端，只验证协议分支，不能证明 Codex UI 已向人类呈现确认。

阶段 4 将 `mcp-server/`、无硬件 `tests/`、文档、固定 SDK 依赖和 sibling `bridge.py` 复制进 `plugins/esp32-codex/` 包。开发时直接运行 server 默认 mock；个人安装脚本在副本内建 venv，安装 pyserial，并把 `.mcp.json` 指向包内 `launch_bridge.cmd`。个人 MCP 配置显式启用 bridge/control/write，读取当前工作目录作为工作区；不会自动连接串口。开始菜单提供真实 Web 面板、MOCK Web 与 Tk 回退。MCP 配置不依赖 `PLUGIN_ROOT` 插值；安装后的源目录需保留，移动或删除时应重装。实际操作仍按工作区策略与严格备份门禁执行。

## 参考事实

- DSH 当前插件：`../dsh-plugin/package.json`、`../dsh-plugin/lib/index.js`，版本 1.2.1。
- 串口桥：`../bridge.py`，依赖 pyserial，按行收发 JSON；行为基线见 [bridge-protocol.md](bridge-protocol.md)。
- MCP SDK stdio 示例和固定依赖版本参考 [OpenAI MCP 连接文档](https://developers.openai.com/api/docs/guides/agents-api/tools/mcp)；Codex 配置字段参考 [Codex 配置参考](https://developers.openai.com/codex/config-reference/)。

## 步骤 5 当前工作区状态（后续实现覆盖以上历史过渡描述）

broker 内部 workspace RPC 提供 current、policy_get 只读及单租约显式 claim/select/policy_set。select 必须在 broker 持有桥断开后执行，先按现有 DSH 工作区特征及 board.json 验证路径、profile 和 .py 入口，成功后只关闭自己的旧桥、切换共享配置并递增 epoch。当前客户端同步本地工作区/profile/入口；其他客户端配置不一致时拒绝加入，不静默切换。claim 排他原子新建 board.json，不覆盖既有；策略用读取时 SHA-256 做乐观冲突检测，保存未知字段及原子更新，坏旧文件拒绝覆盖。MCP 仅公开只读 esp32_workspace_current；修改 RPC 由后续面板本身逐次人工确认后使用。没有真实设备、UI 人工确认通道的验收证据。
### 当前面板步骤 5–10

broker 单租约与断开、共享 epoch/策略版本复核后才认领、选择或写策略；MCP 只提供工作区状态只读工具。窗口使用同一 broker 桥，面板自身逐次确认不替 Codex 对话授权。Windows pythonw 无控制台启动器以用户 SID 派生的独立 mutex 约束单窗口；开始菜单真实 Web 快捷方式、MOCK Web/Tk 回退与 `esp32_open_panel` 共用窗口宿主，不自动连接物理串口。`--probe` 不创建窗口且不连接 broker。个人 CLI 插件缓存已刷新；COM4 真实桥连接/状态/断开通过。桥返回 `physicalStopConfirmed=false`，物理停止未确认；本轮未运行代码、REPL 或板载文件操作。

### MCP 宿主路径（2026-09-28）
个人安装器 `-McpHost Wsl` 通过 `wsl.exe --exec wslpath -a -u` 转换 MCP command/cwd；args 中交给 cmd.exe 的启动器仍使用 Windows 路径。Windows 桥仍运行于包内 Windows venv，无需把串口迁到 WSL。默认 `Windows` 配置仅适用于原生 Windows Codex 后台；客户端界面在 Windows 不代表其后台是 Windows，必须以后台启动日志为准。
