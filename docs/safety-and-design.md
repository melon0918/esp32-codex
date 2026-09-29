# 安全与工具设计规范

## 三档操作策略

| 策略 | 行为 |
| --- | --- |
| `auto` | 按用户请求执行；工具仍要校验路径、参数和连接状态 |
| `confirm-write` | 写入、删除、下载及 PID 修改前取得用户针对本次操作的明确同意；其他动作按现有 DSH 语义执行 |
| `confirm-all` | 除状态、日志读取、断开及停止等安全动作外，执行前取得用户针对本次操作的明确同意 |

策略从当前工作区 `esp32-ide/workbench-policy.json` 读取；缺失、无效或不可读时采用 `confirm-write`。策略更改本身须由用户明确提出。确认应与具体操作及参数绑定，不把模型可自行填写的布尔字段、令牌或随机数视作用户同意。

阶段 3 控制工具的分类：

- `connect`、`run`、`interrupt` 属于控制动作；`confirm-all` 下须逐次确认，`confirm-write` 下沿用现有 DSH 语义不额外确认。
- REPL 可以执行任意 MicroPython 代码，可能写文件或控制硬件；按写操作分类，`confirm-write` 和 `confirm-all` 下都须逐次确认。
- `disconnect` 和 `stop` 属于紧急/释放动作，在三档策略下都不要求额外确认，但仍须检查连接状态并明确报告桥的实际语义。
- 真人确认必须来自经验证的人类交互通道；拒绝、取消、客户端不支持或错误都必须阻止该真人确认路径。模型提供的 `confirmed:true`、token、nonce 不构成真人批准。
- 用户明确授权具体目标与效果范围，并要求 Agent 操作面板时，可由面板专用 Agent 决策接口代办与授权完全匹配的待确认操作。broker 必须绑定原待办的操作上下文并记录 `agent_delegated`；面板按钮决策记录 `human_panel`。Agent 代办不得宣称为真人确认，也不得扩大授权范围、修改策略或默认运行机器人；授权不明确、请求不匹配、过期、取消或错误均须阻止动作。
- 本地 MCP SDK 的 elicitation API 不证明调用方已向人类展示确认。真实 Codex UI 真人确认通道尚未完成实测，实际需要真人决定的动作在验收前仍 fail-closed。模拟客户端接受 elicitation 只验证协议分支，不能作为生产真人授权已验证的证据。
- 真实桥控制工具默认不注册。只有显式添加 `--enable-control-tools` 才能发现并调用 `esp32_connect`、`esp32_disconnect`、`esp32_stop`、`esp32_run`、`esp32_interrupt`、`esp32_repl_send`；该配置不代替操作策略。真实模式下 `confirm-all` 的 connect/run/interrupt 和 `confirm-write`/`confirm-all` 的 REPL 在真人渠道未验收时仍须 fail-closed；与用户授权范围匹配的 Agent 代办只可走上述独立接口，不得把 MCP elicitation accept、模型参数、token 或 nonce 作为真人授权。

## 设备与串口

- `hiwonder` 的停止动作应实际停止两个电机；`generic` 的停止动作仅打断程序，电机或舵机可能保持当前位置。工具结果必须明确区分，不能统一声称“已停车”。
- 即使 `hiwonder` 桥返回 stop 成功，也只证明桥调用已返回；不能据此声称已收到电机实际停转的硬件确认。
- 串口独占。连接失败时不探测、杀掉或断开 DSH 进程；由用户在 DSH 中手动断开后重试。
- 不在测试阶段自动连接真实设备；无设备验证使用模拟桥或录制的协议数据。
- 阶段 2 只列出串口和读取桥状态，不提供连接命令。桥返回未连接、访问拒绝或资源占用时，说明 DSH 可能持有串口并要求用户手动断开；不能声称端口枚举已确认占用进程。MCP 服务只管理自己启动的桥进程。
- 旧 bridge.py 的 `listfiles`/`readfile` 会通过 REPL 访问板子；`readfile` 会中断程序并执行机型停止动作。真实模式只有同时显式启用 `--enable-control-tools` 与 `--enable-write-tools` 才放行文件 list/read；结果需说明读取可能改变运行状态。`confirm-all` 对真实文件 list/read 要求确认；Codex UI 确认能力未验收期间仍 fail-closed。
- 阶段 3a 的 `esp32_mock_*` 控制入口只连接状态ful 模拟器；每个调用在桥调用前检查 `mode=mock` 和 `mock-scenario=control`。真实桥默认只允许 `ports`、`status`；仅显式 `--enable-control-tools` 时才放行用户已授权的控制命令。mock-only 工具在真实模式下必须先拒绝并且不启动桥进程。REPL 模拟器只记录文本，不执行。
- generic 的 stop 只中断程序；interrupt/stop 的响应和旧控制台文字不能证明电机或舵机已安全停下。模拟工具返回 `physicalStopConfirmed: false`，控制台夹具也明确声明不代表实体停转。
- 高影响动作（程序下载、文件写入/删除、PID 修改、重启运行）需在实际设备试验前单独确认目标、影响和回退方式。

## 输入、输出与数据

- 板载路径必须是绝对路径，拒绝路径穿越、引号注入和不支持的文件名；本地下载文件仅允许工作区根目录的 `.py`。
- 每个工具使用明确的 JSON 输入；返回连接状态、机型、入口、端口、校验结果等可核对字段。错误要保留可操作的原因，不泄露环境密钥。
- 用户已确认：覆盖现有目标前必须成功备份；板载读取、本地备份写入或备份校验任一步失败，都必须在发出覆盖命令前中止。只有可靠确认目标不存在时才允许首次创建；如果桥无法区分目标不存在与读取失败，则 fail-closed。legacy `backup` 文件名本身不证明内容已成功保存。
- 阶段 3b 为 `../bridge.py` 增加 opt-in 的 `strictBackup` 路径。MCP 先读 `capabilities`，要求 `codexStrictBackup: 1`；PID 原始读取还要求 `codexRawFileRead: 1`。严格模式会先成功枚举目标父目录，存在时读取原始 bytes 并验证长度/CRC，在工作区根目录独占备份、flush/fsync、回读逐字节和校验长度/CRC；任一步失败都在发送目标 `open(...,'wb')` 或 `os.remove` 前拒绝。现存目标回报 `targetExisted:true, backupVerified:true`；经成功枚举证明目标不存在时回报 `targetExisted:false, backupVerified:false`。PID 写入还携带读取时 CRC，目标内容变化则中止。
- 旧 DSH 命令不传 `strictBackup`，继续使用其已有下载行为（备份失败会提示后继续）及原 write/delete 路径。Codex 写入工具始终请求严格模式，不根据 legacy `backup` 推断备份成功。
- 阶段 3b 将删除也视作可回退操作：删除现存板载文件前完成同等严格的原始 bytes 备份；目标缺失或任何备份步骤失败时不发出删除命令。
- 阶段 3b 使用 capability 握手确认桥支持 Codex 严格路径；不支持时，下载、覆盖、删除和 PID 修改在发出任何 mutation 命令前拒绝。严格路径以板端父目录成功枚举确认目标存在/不存在；对现存目标读取原始 bytes，在工作区根目录以独占新文件保存，flush/fsync 后回读逐字节、长度与 CRC 校验。任何一步失败均不发送覆盖或删除命令。旧 DSH 调用不携带 strict 参数，保留当前语义。
- 日志只记录概要、测试结果和待办；不记录密钥、令牌、完整串口输出、板载程序全文或其他隐私数据。

## 变更纪律

一次只完成 [执行计划](implementation-plan.md) 的一个阶段。任何对 `../bridge.py` 或 `../dsh-plugin/` 的修改必须先说明原因和兼容影响，并单独验证 DSH 行为。不要用测试替代真实硬件安全确认。
