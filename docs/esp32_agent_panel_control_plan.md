# ESP32 Codex：Agent 完整控制面板开发计划

日期：2026-09-28。状态：AC-00–AC-08 已完成开发实现与限定范围验收；RCT-01–06 面板实机点击验收已完成。AC-09 正在按机器人仓库 MD-P11 继续单机真机联调。用户要求 Web 为正式面板；Tk 暂留兼容回退。Windows 原生 MCP、Agent 确认处理、COM4 共享状态与窗口生命周期均已实证；Codex 对话侧真实确认渠道仍未验收。

本计划已从机器人项目迁入本仓库 docs/esp32_agent_panel_control_plan.md，作为 ESP32 Codex 插件的独立开发计划；原维护源位于 [本机路径] 路径 [本机路径] Git 仓库，源树未发现 AGENTS.md。维护源与插件缓存分开，缓存不直接编辑。修改前创建的维护源快照 [本机路径] 排除个人 .mcp.json 和 .venv。此计划记录 MD-P11 的插件工具依赖改造，不改变机器人的多设备协议，也不合并到 MD-P12。

## 1. 问题与已核实证据

- 当前对话已挂载 ESP32 Codex 的 26 个工具。`esp32_status`、`esp32_serial_ports` 返回 `source=bridge_process`、`simulated=false`，说明 MCP 到真实桥的只读调用可用；本轮没有调用 connect，不能据此宣称串口连接成功。
- 共享工作区仍为幻尔小车配置（hiwonder、入口 `/corex.py`）；当前项目 `python版本/源码/quad-mpy-master` 可以识别为四足机器人（generic、preset=quad、入口 `/main.py`），但读取工作区信息不会选中它。
- MCP 只提供 workspace list/info/current，缺少选择、认领和真实模式策略读取。`esp32_mock_policy_get` 在真实桥模式明确拒绝。
- 插件已有工作区选择、认领、策略修改和面板确认实现。现行文档将工作区变更保留为内部 RPC，并要求单客户端租约、串口断开及 epoch 一致；面板和 MCP 同时使用时需要专门审查这一限制。
- 当前面板只确认自身发起的操作；真实 MCP 人类确认通道尚未验收，需确认的部分动作会拒绝执行。仅增加几个工具名称不能解决完整调用链问题。
- Computer Use 曾在当前 WSL 会话因工作目录 URI 初始化失败；Codex 重启后已成功读取 ESP32 工作台窗口截图。截图只能证明窗口可见及其显示内容，不证明板端身份或设备行为。
- `esp32_open_panel` 返回启动请求成功及 `autoConnect=false`；尚无本轮窗口目视证据。当前工具缺口不能依赖窗口点击作为长期解决方案。

## 2. 目标与范围

Agent 能通过结构化 MCP 接口完成面板全部业务操作，并可查询、显示和管理面板窗口。用户在面板中的操作与 Agent 操作使用同一 broker、唯一串口连接及同一操作状态；面板关闭、最小化或 UI 自动化不可用时，业务操作仍可完成。

“完整控制”覆盖当前产品已有的工作区发现/认领/选择、认领时的机型与入口、策略查看与修改、串口连接、程序控制、REPL、部署、板载文件、PID、控制台和窗口操作。已存在的 typed MCP 工具继续提供程序、文件和 PID 能力，不再造一个重复的任意动作入口。保留既有硬件安全约束、备份规则及真实/模拟来源标记；不把完整控制实现成伪造人工确认或自动降低策略。

本次不增加网络监听、远程多人账号系统、第二套串口工具或任意宿主 shell 执行入口。Computer Use 可用于视觉验收，业务验收不依赖它。

## 3. AC-01 能力盘点与冻结接口

AC-01 已依据插件源码 `mcp-server/server.py`、`mcp-server/broker/host.py`、`mcp-server/broker/adapter.py`、`mcp-server/panel/{backend,actions,view}.py`、`web-panel/host/host.py` 及现有测试完成。新 MCP 工具复用 broker/PanelBackend 已有动作；不新增低层 pipe RPC 给模型，也不复制第二套设备控制实现。

| 当前面板能力 | 当前入口 | Agent 可用状态 | AC 实施 |
|---|---|---|---|
| 读取连接、端口、工作区、策略、来源、控制台 | 面板 `get_snapshot`；MCP status/ports/current/console | 分散可读；MCP 无统一快照和真实策略查询 | 加 `esp32_snapshot` 与 `esp32_capabilities`，快照仍只读、有界并带 source/simulated |
| 发现工作区 | 面板 `discover_workspaces`；MCP workspace_list/info | 已有 MCP list/info/current；Windows MCP 不接受 WSL `/mnt/<drive>` 写法 | 保留工具，统一执行端路径规范化并加边界测试 |
| 选择工作区 | 面板 `perform(workspace_select)` → PanelActions → broker workspace RPC | 没有 MCP 选择工具；当前 broker 要求恰好一个 lease，面板与 MCP 并存时拒绝 | 加 `esp32_workspace_select(path, expected_epoch)`；修复多客户端状态一致性，不自动连接 |
| 认领工作区 | 面板 `perform(workspace_claim)` → 原子创建 board.json | 没有 MCP 认领工具 | 加 `esp32_workspace_claim(path, profile, label, entry, expected_epoch)`；不覆盖已有文件 |
| 查看/修改策略 | 面板 snapshot / `perform(policy_set)`；broker 有 policy_get/set | 仅 `esp32_mock_policy_get`，真实桥下故意拒绝；无 MCP policy_set | 加通用 `esp32_policy_get`、`esp32_policy_set(policy, expected_revision, expected_epoch)` |
| 串口连接、断开、运行、中断、停止、单行 REPL | 面板 `perform`；条件注册 MCP typed control tools | 已有 MCP 控制工具；按安装 flag 注册，部分动作受真实确认门禁 | 保留现有工具名、flag 与机型语义；统一从当前共享快照取工作区/profile/entry |
| 下载、文件列表/读取/写入/删除、PID | 面板含 download/download_run；MCP typed file/PID tools | MCP 已有部分能力，写工具需启用 write flag；严格备份在 server/bridge 已实施 | 保持现有工具和严格备份合同；能力快照说明开关及缺失原因 |
| 刷新与控制台查看 | 面板 `get_snapshot` 周期读取；MCP status/console_read | 已可用但没有一个原子快照 | 由 `esp32_snapshot` 返回有界日志增量；复制是 UI 本地动作，无需另建工具 |
| 打开、聚焦、最小化、关闭面板 | MCP `esp32_open_panel`；CompactPanelApi minimize/close | 只能请求打开；没有窗口状态、最小化、关闭工具 | 加 `esp32_panel_status`、`esp32_panel_window(action)`；恢复复用 open/activate；关窗不关共享桥 |
| 面板确认对话框 | 面板内部当前 pending operation + approve/reject | MCP 无可信真实确认闭环；real bridge 的需确认 MCP 操作 fail-closed | AC-04 实现受信确认关联；Agent 不能用参数/工具调用给自己批准 |
| 拖动、选中文字、复制控制台、收起区块 | WebView/Tk 本地交互 | 没有 MCP 对应动作 | 纯显示/输入操作由结构化工具等效，窗口移动不属于业务 API；不增加剪贴板/任意 UI 点击工具 |

AC-01 冻结策略效果：状态、端口、工作区/策略只读查询属于 safe；connect/run/interrupt 属 control（`confirm-all` 下确认）；stop/disconnect 继续即时可用；板载 list/read 属 control（真实 list/read 可能访问 REPL/中断程序）；REPL、下载、板载写入/删除、PID 修改属 write（`confirm-write`/`confirm-all` 按现有分类确认）；workspace select/claim 和 policy_set 继续无论策略档位都逐次确认。`auto` 仅适用于用户当前工作区明确配置的 auto 策略，不允许 Agent 为绕过提示而修改策略。真实确认没有证明由人作出时 fail-closed。

新工具 schema 与错误合同：所有变更要求调用方先取得 `esp32_snapshot.control_epoch`；工作区选择、认领还要带该 epoch，policy_set 另带 policy_get 的 revision。成功返回当前结构化快照、source/simulated 和更新后的 epoch。保留既有 `ok/error` 字段并为新工具附稳定 `errorCode`：`invalid_path`、`workspace_not_recognized`、`workspace_exists`、`epoch_conflict`、`revision_conflict`、`disconnect_required`、`busy`、`confirmation_required`、`confirmation_expired`、`capability_disabled`、`backup_failed`、`broker_unavailable`、`outcome_unknown`。错误和快照不包含令牌、凭据、板载文件正文或完整异常栈。

AC-01 另外确认两项必须修复的跨客户端缺陷：`BrokerHost._workspace_dispatch` 以 `_lease_count == 1` 阻止面板与 MCP 并存时选择；切换只更新发起客户端的 `BridgeClient` 工作区/profile/epoch，其他客户端以及 MCP 的 `tools.entry` 可能保留旧配置。所有客户端需读取 broker 的当前配置，entry 与 profile/workspace 在一个 epoch 下原子同步；启动参数不得在后续工作区选择后覆盖新 entry。

## 4. 架构与状态合同

调用链：Agent MCP / Web 面板 / Tk 回退 → 共用操作服务 → broker 串行仲裁 → 唯一 BridgeClient → 设备。窗口操作由受限宿主接口处理，不让页面取得管道凭据或任意文件/命令能力。

### 4.1 共享会话与工作区切换

- 区分“客户端存活租约”和“变更操作锁”。MCP 与面板可同时在线；选择/认领必须通过 broker 单一操作锁串行化。任何连接中、真实设备动作/文件操作在途或结果未知时拒绝切换。
- 切换请求带目标和 expected_epoch。broker 在同一锁内复核连接/busy/未完成写入/待确认操作状态，准备目标 workspace/profile/entry，提交新上下文并递增 epoch。仅对确实需后台运行且可能超过 MCP 调用时限的动作增加 operation_id；同步动作保持同步，不引入无用任务队列。
- 默认已连接时返回明确的 `disconnect_required`，由 Agent 在授权范围内先断开再切换；不隐式停车、断开或切换其它端口。设备动作、写入或结果未知时禁止切换。
- 竞争切换只有一个提交成功，另一请求收到冲突及最新快照；不能出现 profile、entry、workspace 各指向不同项目的中间态。
- 切换使旧工作区待确认操作失效；所有客户端下一次读取都从 broker 得到新快照，旧页面或延迟 MCP 请求因 epoch 不同而拒绝。失败保留原上下文，返回稳定错误码和可恢复原因。
- 面板关闭只释放自身客户端资源；broker 生命周期根据实际剩余客户端管理，避免关闭面板导致 Agent 断连。

### 4.2 路径与配置

- 在执行端规范化路径，提供明确的路径风格/宿主信息。支持已验证的 `/mnt/<drive>/...` 到 Windows 盘符转换；不盲猜 UNC、任意 Linux 路径或远端主机映射。
- 中文、空格、大小写/盘符等价路径应识别为同一工作区。无法转换时返回 actionable error，禁止选到默认项目。
- 配置写入按 revision 比较并原子提交；原子能力不足则明确失败。保留已有字段，拒绝非法配置和不安全符号链接，不静默重建损坏配置。
- 用户配置与安装缓存解耦；升级后保留选中工作区及策略，重启不自动打开串口。

### 4.3 授权与确认

- 已有用户授权覆盖的日常操作应能直接推进，避免每次切换工作区都要求用户点击面板。必须明确区分产品策略确认与设备写入/运动授权。
- 只读、工作区选择/配置、策略修改、连接/运行、REPL、文件写入/删除分别定义效果类别；AC-01 固化现有 confirm-write/confirm-all/auto 的逐项语义及迁移规则。
- 策略修改是独立操作；Agent 不得把策略改成 auto 当作继续某次操作的前置技巧。用户明确要求改策略时展示变更并通过相应受信授权渠道。
- 需要确认时返回结构化 `confirmation_required` 和操作 ID、目标、影响、参数摘要、epoch、revision、期限。确认记录绑定这些值，任何变化均失效，一次消费，不可重放。
- Agent 可以发起、查询、取消确认。用户明确授予具体目标/效果范围并要求 Agent 操作面板时，Agent 可通过面板专用决策接口处理与授权范围完全匹配的待办；必须记录为“Agent 按用户授权代办”，不能声称真人点按/批准。Agent 不得把委托扩大到未授权目标、策略变更或运行机器人，也不得伪造真人确认渠道验收。
- 为需要人工确认的动作实现并验收真实客户端确认渠道：优先使用可证实来源的宿主确认机制；若 MCP elicitation 无法证明人工来源，则使用与具体操作关联的面板人工确认。面板确认 Agent 操作须作为显式功能开发，不能沿用“只确认自身操作”的旧合同。
- 若宿主支持已授权范围的受信会话授权，应绑定工作区、效果、有效期及撤销方式，减少重复确认；普通聊天文字、模型生成 token 不自行充当 broker 的可信凭证。
- 缺少可信确认渠道时保留明确阻塞原因；不得声称整个“完整控制”已验收。需要人工决策与需要用户代替 Agent 操作面板应在 UI/响应中清楚区分。

### 4.4 长任务、错误与秘密数据

- 长任务返回 operation_id，可查询阶段及有界事件；重试先查询，不能盲目重放 REPL、下载并运行或运动。服务重启后的未知结果必须显式报告。
- 标准错误至少覆盖：工作区无效、路径不可转换、epoch/revision 冲突、设备忙、端口占用、需断开、需确认、确认过期、能力不支持、备份失败、结果未知。
- 读取板载文件可能中断程序，须按实际效果标记，不能仅因工具名带 read 就视为无影响。
- WiFi/API 配置和租约令牌不进入快照、日志、错误或操作列表；敏感文件支持仅字段状态/摘要的检查方式。普通源码读取与敏感内容返回规则分开设计。
- 保留 COM4 单桥独占、严格备份及机型停止语义；stop 响应不能代替现场观察或物理断电。

## 5. 单 Agent 分步实施

一次完成并验收一项，更新本仓库 status/devlog 后再推进。AC-00–AC-07 已完成开发和对应隔离验证；AC-08 已在用户授权范围内完成单板真机验收，详细证据见当日日志。真人手动确认渠道仍未验收；AC-09 待办。

| 步骤 | 交付 | 依赖及退出条件 |
|---|---|---|
| AC-00 | 本计划、机器人项目入口与阻塞记录 | 完成：主计划落盘，工具/工作区缺口可复现；只读检查 |
| AC-01 | 面板/MCP/内部 RPC 全量能力矩阵、确认效果表、API/schema/错误码、源目录核实 | 完成：能力矩阵和错误合同已冻结；维护源非 Git，基线快照已保存；未改代码 |
| AC-02 | broker 共享 workspace/profile/entry 快照、epoch/revision 与多客户端切换仲裁 | MCP+面板并存时仅一次提交；竞争/断开/进程退出故障注入通过；连接/忙碌拒绝；保留唯一桥 |
| AC-03 | 工作区认领/选择、策略读取/修改 MCP 工具及 WSL/Windows 路径解析 | 完成：4 个 MCP 工具加入；窄 WSL 盘符映射；路径 8/8、控制工具 11/11、broker 27/27；语法检查通过。真实桥写操作仍 fail-closed，见 AC-04 |
| AC-04 | 通用操作授权、真实确认渠道、策略修改接口 | 完成开发与隔离验证：一次性面板确认绑定请求参数和 workspace/profile/entry/epoch/policy revision；Web/Tk 面板人工决策入口、MCP 只读状态/取消入口已接通且没有 MCP 批准入口。假桥覆盖批准、拒绝、取消、过期、重复消费、策略/epoch 变化及工作区切换；真实可见人工点按和真机行为留待 AC-08 验收 |
| AC-05 | 现有程序、串口、REPL、文件、部署、PID MCP 工具能力自描述与状态同步 | 完成：新增 capabilities/snapshot；注册 flag、profile 限制、结果未知和敏感输出合同已覆盖。Windows mock 定向只读 13/13、写入/PID 6/6；既有 strict bridge 故障注入受 AC-07 源目录路径问题阻塞，未宣称本次复跑 |
| AC-06 | Web 主面板窗口状态/生命周期 MCP 工具及状态同步 | 完成：status/control 支持 open/focus/minimize/restore/close；标准 WM_CLOSE 释放自身 lease；fake Win32 3/3、MCP tool discovery 1/1、broker idle/heartbeat 1/1、peer lease 1/1。真实可见窗口点按留待 AC-08；Tk 暂保留至 Web 验收后再退役 |
| AC-07 | 跨入口与打包回归、文档/技能更新、升级与回退包 | 完成开发范围：修复 standalone 源布局测试；Windows 临时暂存包 probe、完整控制面核心工具 MCP 发现及 142/142 mock/假桥全套通过。未改个人安装/缓存；当前会话 WSL→Windows schema 重挂载、可见 Web 面板、安装/回退演练留待 AC-08 验收 |
| AC-08 | 经授权的单板真实验收及交付记录 | 限定范围完成：Windows 原生 MCP 发现 36 工具；共享工作区切至四足 generic `/main.py`；COM4 真实连接、MicroPython 身份、状态和断开通过；板载文件 list/read 与临时探针 write/read/strict-backup-delete 通过。Agent 在用户授权下通过面板专用接口处理精确请求；不是真人点按验收。板载 `/main.py` 与本地源码摘要不同，未覆盖或运行；本地严格备份留存。 |
| AC-09 | 回到机器人 MD-P11，整理交付 | 进行中：已确认机器人仓库要求及 MD-P11 范围；按单机固件部署、真实身份/租约、网络及动作验收步骤推进；MD-P12 仍另定现场范围 |

### AC-09 继续：MCP 只读查询恢复 broker lease — 完成

- 目标：修复 MCP 客户端丢失命名管道 lease 后，面板/MCP 状态、串口和控制台查询一直报 `client has no broker lease` 的问题。
- 验收标准：lease 丢失后的安全只读查询自动建立新 lease 并返回共享状态；同时在线时恢复到同一个 broker/backend；控制与文件操作不得因重连而自动重放，调用方先收到明确错误，再可读取状态；相关 broker 集成测试和完整插件测试通过。
- 范围：仅修改 broker 客户端重连路径及其测试/日志，不连接串口或触碰设备。本阶段完成后再继续 AC-09 实机流程。
- 结果：只对状态、串口、控制台、当前工作区/策略及确认状态读取自动恢复；控制、文件、策略变更和确认变更不自动重连或重放。主动关闭的客户端仍保持关闭。生成插件包完整测试 149/149 通过；故障用例在源树和插件包各 1/1 通过。
- 当前 Windows MCP 工具进程仍是旧代码；刚才 `esp32_status` 实测仍返回 `client has no broker lease`。同步安装副本并重启 MCP/面板后再作实机续测。

面板状态最终更新（2026-09-28）：AC-08 日志初稿记为“可见但不在前台”；随后 Agent 调用 `esp32_panel_control(focus)` 返回成功，最终窗口 `foreground=true`。

实施落点预计为插件 `mcp-server/server.py`、`workspace_control.py`、`policy.py`、`broker/`、面板共用 backend/actions、`web-panel/host/host.py`、Web/Tk 界面、启动/安装脚本、技能与文档。AC-01 以实际源码确定文件清单，避免直接编辑本次查看的安装缓存。

## 6. 验收矩阵

| 编号 | 必须证明的结果 | 层级 |
|---|---|---|
| AP-01 | 所有面板动作均有 Agent 接口或明确的等效操作；真实模式能力自描述与工具列表一致 | 静态/协议 |
| AP-02 | MCP 和面板同时在线时，从幻尔工作区切至四足工作区；两边路径、profile、entry、epoch 一致 | mock 集成/真实环境 |
| AP-03 | 两客户端同时切换仅一个成功；切换后旧确认/旧 epoch 请求拒绝；失败保留原工作区 | 故障注入 |
| AP-04 | 已连接、忙、下载中、结果未知时切换受控；无隐式断开/执行/重放 | mock/单板 |
| AP-05 | 中文/空格/WSL 盘符映射通过；错误映射、损坏配置、符号链接和 revision 冲突不覆盖原配置 | 本地 |
| AP-06 | 真实与 mock 策略读取可用；策略改变不作为绕过确认手段；全部效果类别按冻结表执行 | 协议/确认渠道 |
| AP-07 | 真正的人类批准才放行需要确认的动作；取消、过期、参数变更及伪造批准不触发设备命令 | mock/真实人工 |
| AP-08 | 下载/文件/PID 写入保留备份与校验，失败可恢复；运行结果未知时不自动再运行 | 假桥/授权单板 |
| AP-09 | 面板关闭/最小化/再次打开、MCP 重连和 broker 重启有明确状态；COM4 不出现第二连接 | 集成/真实环境 |
| AP-10 | 不使用鼠标自动化完成“查状态→选择小T→查策略→连接→查状态→断开”；面板同步显示 | Windows/WSL |
| AP-11 | 快照、错误、日志、控制台与操作记录按约定脱敏；敏感配置只输出字段状态 | 隔离 |
| AP-12 | 打包后新增工具实际挂载；保留用户配置；回退可用；无残留串口/进程/计时器 | 发布验证 |

每项记录版本/源码摘要、环境、真实或模拟、操作序列、预期/实际、脱敏证据和清理结果。mock、真实 MCP 通路、真实人工确认、真实设备分别记录；任何一层缺失均不得以其它层替代。

## 7. 交付完成标准与当前状态

完成标准：全部面板能力有可用 Agent 入口；工作区切换和真实策略读取在当前 Windows 原生场景可用；MCP 与面板共享状态且不会互相阻塞；授权/确认、备份、并发和生命周期验收通过；正式安装后能以结构化接口恢复 MD-P11。

AC-00–AC-07 已完成开发实现与本地隔离验证。AC-07 修复了 standalone 源布局下的测试路径问题；Windows 临时 Python 环境全套 unittest discover -s tests -v 142/142，launcher + strict-backup 定向 16/16；临时包 MCP 工具发现和 Web probe 通过（默认不建窗、不自动连接）。插件工具文档与 Agent skill 已更新。验证使用临时完整源树；未修改个人安装/插件缓存，也未做 rollback 安装演练。以上为 AC-07 历史验收。AC-08 最新实机结果：Windows 原生安装配置可启动 MCP；桥共享工作区切为 [本机路径] 返回 MicroPython v1.24.1，随后已断开；板载列出 7 项，`/main.py` 为 1301 bytes、SHA-256 `3af1f9023e643c0819b2cf1a535a7f7da9a20950a2007a7a6190245341d42caa`。临时注释探针 74 bytes 写入并回读完全一致，随后在 `backupVerified=true` 后删除；板端复查无探针，本地严格备份留在工作区根目录。板载 `/main.py` 与本地 2681-byte 源码不同，没有覆盖、下载或运行机器人程序。用户明确要求 Agent 控制面板并授权桥连接/固件读写，Agent 经面板专用决策接口处理与授权范围完全匹配的请求；未由真人点按，不计作真人确认渠道验收。MCP 面板 launcher 首次只报告 `launchRequested`，随后通过 Windows 原生 `pythonw.exe` 打开面板并核对共享工作区；验收后面板可见但不在前台，COM4 `connected=false`。AC-09 待办；Web 为正式面板，Tk 暂留兼容回退。
