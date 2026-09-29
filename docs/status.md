# 2026-09-29 最新状态：RCT-01–06 完成；AC-09 / MD-P11 进行中；AC-10 完成

UI-14 连接诊断：COM4 的 CH340 枚举正常且可由 pyserial 约 0.01 秒打开；发送中断后 4 秒未收到任何串口数据。旧 broker 的控制 RPC 默认 15 秒等待与桥 `connect` 15 秒预算重叠，导致客户端看到 `outcome_unknown`。修复并重载后，真实 COM4 约 16 秒返回桥的“板子无响应或固件异常/回显不完整”错误，后续状态 `connected=false`。插件错误回传缺陷已修复；板端供电、固件或 CH340 至 ESP32 的 UART 链路故障点尚未定位，不将 USB 枚举成功等同于 ESP32 正常响应。当前未运行程序、写板或操作机器人。

2026-09-29 重启复测：重启后共享工作区回落到 IDE 根目录 `hiwonder`/`/corex.py`，未在身份不匹配时连接；经 Agent 面板决策切换回四足 `generic`/`/main.py` 后，修复版 MCP 返回明确 `bridge_error`，MicroPython 探测命令无回显。Windows PnP 的 USB-SERIAL CH340 (COM4) 为 `Status=OK`、`ProblemCode=0`、驱动 `CH341SER_A64` 版本 `3.9.2024.9`。随后用户授权复位；临时 esptool 5.4.0 的只读 `chip-id` 返回 `Wrong boot mode detected (0x13)`：MCU ROM 已通过 COM4 响应，但自动复位没有进入下载模式。未烧录 Flash、读写板载文件或运行程序。舵机仍连接，进一步复位/启动前需由用户确保机构安全并手动按住 BOOT、点按 EN/RESET 进入 ROM 下载模式；随后可用 `--before no-reset` 读取芯片标识。

AC-10 已完成：`esp32_panel_agent_decide` 按 broker lease 隔离待办，并区分 `agent_delegated` 与 `human_panel`。broker 30/30、MCP 控制工具 18/18、面板确认 7/7 定向测试通过；完整发行包测试 151/151 通过，validator 通过。授权重载旧 broker 后，在新 MCP 会话中恢复四足 generic `/main.py` 工作区；Agent 同会话批准工作区选择和一条只读 WLAN REPL 查询，来源均为 `agent_delegated`；`confirm-write` 下 COM4 连接无需单独确认。REPL 只发送一次，控制台读到 `STA`/`AP` 标记；结束后 COM4 已断开，Web 面板已重开且 `autoConnect=false`。假桥测试已覆盖真人面板按钮仍标为 `human_panel`。Codex 当前任务的工具目录仍需新任务/重载后刷新；真人确认与 Codex 对话侧门禁仍单独验收。

AC-09 broker 恢复修复已在源码和生成插件包验证：失去 lease 后安全只读查询可建立新 lease；控制/文件请求不自动重放；插件包完整测试 149/149 通过。源树、GitHub 克隆和个人安装副本的 broker client SHA-256 一致。当前 Codex MCP 工具的状态/端口/快照调用仍返回 `client has no broker lease`；短暂 fresh BrokerClient 可取得只读 lease 并确认共享 bridge 已断开、COM4 可枚举。Web 面板打开且可见，Windows 拒绝置前请求。板端 `/identity` 主机请求超时，MD-P11 身份/租约与网络验收仍未完成。独立源码已推送 commit `51f46c3`，PR [#1](https://github.com/melon0918/esp32-codex/pull/1) 已创建并保持打开。修复及验收细节见专项计划和当日日志。

UI-13 已完成：Web 面板现在保留桥/Broker 超时或传输失败的“结果未知”语义，并提示先刷新状态、不要重放。项目源和自包含发行包的控制/确认定向回归各 23/23 通过；无模拟 broker 残留。项目级令牌/PID 握手简化与快照失败时的模式标记仍未处理。

RCT-01–06 已按 [面板实机点击验收计划](panel-real-click-test-plan.md) 完成。最终真实窗口对应四足 generic `/main.py`、策略 `confirm-write`，启动时 `autoConnect=false`；COM4 已断开，验收窗口和 MCP helper 已退出。Agent 确认批准/拒绝、安全 REPL 标记、工作区上下文、生命周期及 MCP 启动入口均由真实窗口和 broker 会话核对。完整 mock/假桥测试 146/146；启动器/WebView2 Runtime 定向 12/12；源码、生成包、Windows 安装副本按打包清单 SHA-256 一致。AC-09 正在按机器人仓库 MD-P11 进行单机真机联调，MD-P12 未批准；Codex 对话侧真人确认渠道仍单独标记未验收。

## RCT-05 实机闭环

- 可见 Agent 确认框显示精确动作、目标、影响、工作区/profile/entry/epoch/策略上下文和有效期。Agent 批准安全 `print` 请求后，COM4 控制台出现对应标记；第二个同类请求被拒绝，拒绝标记未执行。策略已恢复为 `confirm-write`。
- 实际最小化、恢复、聚焦和关闭面板；关闭时独立 MCP 客户端仍能读取 `connected=true` 的桥状态，随后经该客户端断开并核对 `connected=false`。关闭面板不关闭其他客户端的连接。
- 修复 MCP 启动使用的 Python 环境，并让 WebView2 Runtime 检查在 MCP 宿主缺少 `ProgramFiles` 环境变量时回退到系统盘标准安装目录。`esp32_open_panel` 现在先读取 broker 的当前工作区/profile；重新选择四足 generic 后，真实 MCP 打开的窗口展示正确的 `/main.py` 与工作区。启动保持 `autoConnect=false`。
- 真实启动后通过 UI 树核对窗口标题、工作区、策略、串口未连接和控制台状态；MCP `esp32_panel_status` 返回 `open=true`、`visible=true`、`foreground=true`。未认领工作区、未创建新的 `board.json`，本步没有连接设备或写入板载固件。

## RCT-06 最终回归

- Windows 插件完整 unittest：146/146 通过；启动器/WebView2 Runtime 定向 12/12 通过。运行中有既有 Pydantic 未解析 lifespan 提示与 subprocess `ResourceWarning`，均未导致测试失败。
- 项目源与 Windows 正在使用的个人插件之间已同步本步涉及的 MCP server、launcher、WebView2 host、回归测试和界面资源；哈希核对/包构建证据记录在当日日志。

## 历史验收快照（后续结论见本页顶部）

真实 Windows 工作台显示 COM4 时，“连接”仍被禁用；实际点击“刷新”也未恢复。根因是 `compact_live.js` 的按钮状态函数把 CSS 选择器传给 `getElementById`，每轮快照后抛错，错误分支又将后端标为不可用。已把该处改为 `querySelector`，同步项目源、项目发行包和当前 Windows 运行源。重启实际窗口后，Computer Use 可访问树显示“连接”已启用；Agent 真实点击 COM4 连接，面板显示“已连接 · COM4”，独立共享 broker 快照 `simulated=false, connected=true, port=COM4`；再点击断开，面板与 broker 均回到 `connected=false`。没有运行、REPL、写板或下载。后续逐步验收见 [实机点击计划](panel-real-click-test-plan.md)，当前下一步 RCT-02。

修复真实 bridge 模式下 `mock_scenario` 被误计入 broker 活动运行配置的问题。MCP 默认 `readonly` 与 Web 面板 `fileops` 现在可加入同一 bridge lease；实际运行开关等配置不同仍被拒绝，mock 模式场景隔离不变。项目源 broker 回归 24/24、当前 Windows 运行源 29/29 通过，项目发行包已从源目录重建并校验关键源码/测试哈希一致。

UI-12 当时通过另一 API 实例取得真实只读快照：四足 generic `/main.py`，`source=bridge_process`、`simulated=false`、`connected=false`。当时没有操作真实窗口；由快照推断连接按钮可用的结论无效。真人确认渠道仍未验收，AC-09 待办。

## AC-08 实机验收（限定范围完成）

AC-00–AC-07 的开发及 mock/假桥隔离验证记录保留。Codex 已切回 Windows 原生；个人插件 MCP 配置使用 Windows `cmd.exe` 与 launcher。当前真实 MCP 会话发现 36 个工具，`source=bridge_process`、`simulated=false`。共享工作区已由幻尔切至已识别的四足工作区，profile=`generic`、entry=`/main.py`、preset=`quad`，面板与 MCP 读取同一工作区/epoch。

COM4（USB-SERIAL CH340）连接成功，桥识别 `MicroPython v1.24.1 on 2024-11-29 esp32`；真实板载目录列出 7 个文件，含 `/main.py`。对临时探针完成写入、回读校验，再以 `backupVerified=true` 的严格备份路径删除；板端复查已无探针。严格备份留在本机工作区，未加入插件仓库。

板载 `/main.py` 与本地入口不同，因此没有覆盖、下载或运行主程序。未运行机器人程序。验收后已断开 COM4，最终 `connected=false`；Web 面板仍打开。用户明确授权的操作由 Agent 经面板专用决策接口按目标/效果/epoch 精确核对后代为处理；这不是用户手动点按，不能算作“真人确认渠道”验收。完整证据见 `devlog/2026-09-28.md`。AC-09 待办。


## UI-11：WSL 启动路径修复（历史验收记录）

安装器支持 WSL MCP 宿主路径转换；修复后从 WSL 使用最终个人缓存配置完成 initialize 和 tools/list，发现 26 个工具，进程正常退出。PowerShell 解析及启动/打包定向 9/9 通过。此项没有调用设备工具或连接串口。完整记录见 devlog/2026-09-28.md。

## UI-10 真机版交付记录（历史验收快照）

UI-10 交付记录（2026-09-28 历史快照）：个人安装与 Codex 插件缓存版本和插件 manifest 一致；`codex plugin list --json` 显示个人 marketplace 中的 `esp32-codex` 已安装并启用。个人 `.mcp.json` 指向 `launch_bridge.cmd`；固定依赖 pyserial 3.5。主开始菜单入口启动真实 Web 面板，MOCK Web 与 Tk 回退保留；`esp32_open_panel(ui="web")` 返回 `simulated=false`、`autoConnect=false`，实际窗口进程标题为“ESP32 工作台”。从安装包真实启动器建立 MCP ClientSession，26 个工具可发现，COM4 返回 `source=bridge_process`、`simulated=false`；连接状态读到 `MicroPython v1.24.1 on 2024-11-29 esp32`，随后断开并确认 `connected=false`。启动器自动选择工作区检测到的 hiwonder profile；断开响应 `physicalStopConfirmed=false`。没有运行程序、REPL 或进行板载文件操作。完整测试 114/114、启动/打包定向测试 9/9、官方插件 validator 与个人包/缓存 12 项关键哈希均通过。旧 marketplace override 指向已过时的 WSL 个人路径，导致 marketplace 加载失败；移除该 override 后默认个人 marketplace 正常加载并成功刷新版本。Computer Use 截图因 node_repl 不接受当前 WSL 文件系统 URI 未能采集；面板进程已实际启动，需从新线程加载新 schema。

2026-09-26 UI-9 个人安装与默认网页面板切换已完成 mock 范围验收。个人安装版本为 `0.2.0+codex.20260926151926`，安装前完整备份已校验；marketplace 注册保持原样。开始菜单主入口运行 Web MOCK，Tk 回退入口显式运行 Tk MOCK；两种实际启动器都成功打开窗口并在关闭后退出 0。默认 WebView2 截图 `web-panel/review/ui9-personal-default.png` 为 874×929 物理像素、DPI 192，中文清晰、MOCK 标识可见。默认与 Tk `--probe` 都报告 `autoConnect=false`、`guiCreated=false`；MCP 工具测试隔离下发现 `esp32_open_panel` 的默认 `ui=web`，不会在测试模式启动窗口。Windows 定向 launcher 9/9、完整 mock/假桥测试 114/114、插件结构 validator 通过；源码/开发包/个人安装 13 个关键文件哈希匹配，pywebview 可导入。PowerShell 5.1 需安装脚本带 UTF-8 BOM，已修正并复装。没有启动真实 bridge、连接串口、操作 ESP32 或修改 DSH。当前 Codex CLI 不在可用 PATH/安装位置，未能刷新 Codex 插件缓存；现有 MCP 服务进程也未中断或重启，新工具 schema 需由 Codex 新线程/重载后获取。实机只读仍等设备及工作区，控制、REPL 与写板仍需单独授权。

2026-09-26 UI-7 紧凑单页 MOCK 初稿：工作区、串口、程序操作、常驻控制台与 REPL 同页，高级设置默认折叠；串口输出可选择后 Ctrl+C 复制，并有“复制输出”按钮。按用户反馈移除 Windows 原生标题栏，换成网页风格的自绘标题栏，提供最小化/关闭按钮，品牌区可拖动窗口。Windows WebView2 实际验证了最小化、还原、关闭、拖动及控制台复制。最终截图为 874×960 物理像素、DPI 192，见 `web-panel/review/ui7-custom-titlebar.png`；窗口启动记录见 `ui7-compact-result.json`。所有控制项仍是独立 MOCK 原型，没有调用 bridge、broker、串口或设备；待用户评审，尚未接入产品界面。

2026-09-26 UI-6 前台 MOCK 验收更新：用户允许可见窗口后，隔离 WebView2/模拟 broker 实测原生鼠标下载→取消→重试→确认，共 4 次系统鼠标点击，下载严格备份模拟成功，窗口退出码 0；捕获 8 张真实前台窗口 PNG（含 700×700、125%/150% 缩放）和结果 `web-panel/review/ui6-visible-result.json`，DPI 192（200%）下已修复截图 DPI 适配、中文标题进程传递、只读/操作区启动时序及窗口工作区适配。临时 `.lnk` 从暂存包打开 Web 面板，二次快捷方式保持相同 PID；经暂存包 MCP `esp32_open_panel(ui="web")` 后从最小化恢复同一窗口并获得前台焦点，记录 `ui6-entries-result.json`、截图 `ui6-visible-entry.png`。当前操作仅 MOCK，未覆盖用户个人安装，也未使用实体设备。此阶段可见范围验收已通过，个人正式安装及更改默认 UI 尚未执行；Tk 继续默认与回退。


2026-09-26 UI-6 隐藏视觉交互追加验收：Windows 实际 pywebview/WebView2 以 hidden=true、focus=false 运行，加载当前操作页面后通过 JS 调用真实宿主及 mock broker，读到 MOCK0；下载确认弹窗、busy 禁用、取消释放、重新批准并完成严格备份模拟下载均成功且进程返回 0。另以隔离 Edge headless/CDP 注入仅供页面验证的 mock API，25 项断言通过并产出 9 张真实像素截图，覆盖主视图、操作区、确认、错误、静态设置/控制台及 700/560/467px 布局；对应文档滚动宽度 685/545/452px，未见页面横向溢出；资源请求仅 1 项本地文件，无外部资源。发现并修复端口卡片“已连接/未连接”文案冲突，以及静态演示折叠时侧边栏切到不可见示例的问题；复测 host 8/8、动作 3/3、原项目全套 114/114 均退出 0。截图和结果记录在 `web-panel/review/ui6-headless-*`。以上是隐藏 WebView2 实际 API 交互及 Edge 无头像素/脚本验证，不能代替可见原生窗口的人眼/人工点击、实际安装/双入口聚焦；UI-6 最终默认切换仍待这些项目，Tk 不变。未访问实体设备或串口。

2026-09-26 本轮补充：单 Agent 已完成 UI-4 无硬件/隐藏验收与 UI-5 双入口、源码包集成，Windows 后台最终复测：launcher 9/9、host 8/8、mock actions 3/3、项目全套 114/114，均退出码 0。暂存包六份关键源/包 SHA256 相同，包内 `--ui web --probe` 确认 guiCreated=false、autoConnect=false；隔离 venv 安装固定依赖后，宿主 `--probe` 显示 pywebview 6.2.1、WebView2 Runtime 可用且 httpServer=false。UI-6 无头 Edge 虽退出 0，但未生成截图，未获可见窗口/人工交互/实际双入口聚焦证据；默认保持 Tk。未安装或覆盖个人插件、未操作真实桥/串口/设备。

用户确认 UI-1 网页风格视觉方向可继续开发；主 Agent 先前以 Windows Chrome 对主视图、工作区、控制台、确认、错误与忙碌状态截图复核，700×700 主视图完整，560 与 467px 宽度无水平溢出，页面无外部请求。**UI-1 至 UI-4 无硬件范围验收通过；UI-5 代码与隐藏打包/依赖验证完成，UI-6 前台验收及默认切换尚未完成。** UI-2 验证 pywebview/WebView2 独立窗口、双向消息与无监听；UI-3 的只读页面已从现有 PanelBackend 获取 mock 工作区、策略、串口、状态与控制台快照，共享 broker 且关闭后只释放自身租约。网页 mock 操作及单实例启动器显式 `--ui web`、MCP `esp32_open_panel(ui="web")` 和安装脚本第二快捷方式已接入；未实际安装或可见验证双入口，Tk 仍为默认和回退入口。当前可运行产品界面仍是 Tk mock，步骤 10 的真实桥只读试点继续等待实体设备和工作区。

更新：2026-09-26（Asia/Shanghai）。当前已批准的独立面板开发步骤 0–9 **已完成无硬件/后台隐藏范围内的实施和主 Agent 验收**。步骤 9 最终全套 112/112 通过、退出码 0；官方插件结构校验通过，包内 mock SDK 发现 26 个工具，18 个关键源/包文件 SHA256 一致，测试 broker 残留 0。用户已授权步骤 10，并确认当前没有连接实体 ESP32。用户指出首轮可见 mock 面板字符不清晰；主 Agent 截图确认后将中文字体统一为 Microsoft YaHei UI、增大正文并收紧间距。Windows 隐藏面板相关测试 21/21 通过，空工作区与长路径布局请求均为 696×683，小于 700×700 窗口；重新打开的 mock 面板实际截图为 700×700、DPI 96，未见裁切。COM6、COM7 都是蓝牙标准串口，尚无可确认的实体板卡端口或指定工作区。没有运行真实桥、设备控制或写板；控制、REPL 和写板试验仍需分别授权。实际个人安装、开始菜单入口、Codex UI 可信确认和实机行为仍未验证。

历史首版基线为 FastMCP stdio、默认 mock fileops；当前唯一 broker 已整合只读、控制、文件/PID、工作区管理，Tk 只读信息窗口已落地。历史 53/53、72/72、78/78、85/85 不代替本轮 93/93 证据。`git status --porcelain` 退出码 128，目录及祖先无 `.git`；不得声称 Git 干净或判断未提交改动归属，继续依据读取版本/清单/哈希保护既有改动。全部测试仅 mock/假桥，无实机。

`.codex/config.toml` 与 `implementer.toml` 历史解析通过并声明 gpt-6-luna/high；当前 WebCodex Runner 报告 `coding_agent_providers=[]`，会话无可用 spawn 接口且 Runner PATH 找不到 `codex` 命令，因此配置热加载与模型/强度实际指定均未证实。用户现已明确批准本次开发全部由主 Agent 实施，当前不使用也不声称启用 Luna High 子 Agent；每步仍要求独立复测和验收。全部后续命令后台隐藏运行，不弹出控制台或 Tk 窗口。真实 UI 确认及实机行为仍未验证。

步骤 1 已新增 `mcp-server/broker/` 的 Windows 用户级单实例命名管道 broker、版本/令牌鉴权、租约心跳、mock bridge 所属 Job 对象清理及 8 个协议/进程测试。尚未接入 MCP 路由或 UI。主 Agent 使用个人插件 venv 独立运行新增 8 项和全套 61 项测试，均退出码 0；检查后无遗留 broker/mock 进程。真实设备、窗口和本会话 `.codex` 热加载仍未验证。

步骤 2 当时把 MCP 公共只读状态、串口和控制台接入 broker，两客户端共享缓冲与游标，配置冲突拒绝；其定向 22/22、全套 67/67 曾通过。当前步骤 4 已移除 write-enabled 的本地桥过渡，所有 MCP 桥命令均由共享 broker 串行执行。单实例集成测试必须串行，Windows 后台桥使用 `CREATE_NO_WINDOW`。

步骤 3 跨客户端 epoch 修复已验收。步骤 4 共享文件/PID RPC、严格备份和能力复核、同桥串行、超时处理、1 MiB 单帧/2 MiB 逻辑消息分帧及背压重试已实现；曾在新 1 MiB 边界测试中发现 PIPE_NOWAIT 写入零字节被误报断管，修复后边界复测 2/2 与全套 78/78 通过。原 strict DSH 兼容测试仍通过，测试后未见残留带 test-identity-sid 的 broker 进程。原有用户 MCP 进程不主动结束；旧 broker 需安全释放现有连接后重启以支持新增 file RPC。下一步按照步骤 5 合同实现工作区选择/认领及策略同步，仅 mock/假桥，GUI 和实机仍未验收。

步骤 5：新增 workspace_control.py（board.json 不覆盖的原子认领、策略哈希比较与原子保存）、broker 内部 workspace RPC（仅单租约/断开设备才变更）及 adapter API；MCP 只新增只读 esp32_workspace_current，不将原始变更 RPC 注册给模型。初次全套因现存用户级 broker 配置冲突出现测试失败；排查 MCP SDK stdio 默认不转发自定义环境变量，改为只供测试用的隐藏 --test-broker-seed 显式传参，真实模式仅允许临时 canned bridge 使用隔离 SID。独立定向 6/6 和最终全套 85/85 通过，退出码 0，测试 broker 残留 0；未终止用户原有 MCP 服务或访问真实设备。步骤 6 可开始，但可见窗口验收必须按用户要求使用后台、离屏/隐藏方式，不允许弹出到前台。

步骤 6：已落地 panel/backend.py 与 view.py；新增关闭面板仍保留另一客户端共享桥租约的测试。hidden root 下检查机型/入口、连接、串口、来源、策略、console 及错误反馈；定向 6/6、全套 93/93，通过，退出码 0，测试 broker 无残留。只读功能无硬件验收通过；真实前台视觉禁止测试。下一步步骤 7 仅实现面板操作界面与面板自身确认，Codex 侧仍 fail-closed。

步骤 7：保留现有 `panel/actions.py` 的逐次确认和 UI，追加私有 mock broker 拒绝连接/运行不执行、确认下载严格备份集成用例，新增只读工作区识别及切换后的入口/策略同步，未选工作区的非应急控制拒绝。hidden Tk 不映射窗口；定向 14/14、全套 105/105，均退出码 0，测试 broker 残留 0。面板自身的真实 UI 确认仅由窗口调用，Codex 对话端仍 fail-closed；步骤 8 待实现双入口和安装包。

步骤 8：新增 panel/launcher.py（独立用户级 mutex、默认 mock、probe 无 Tk/无自动连接、pythonw 无控制台请求），MCP 增加 esp32_open_panel，隔离测试服务禁止启动 UI；安装脚本可在用户显式执行时创建个人开始菜单入口且无 Force 时拒绝覆盖既有包/快捷方式。开发态构建 plugins/esp32-codex 源码包成功，source 与包内服务、broker、面板、测试关键哈希一致，安装脚本 PowerShell 解析 0 错，包内 probe 输出 guiCreated=false、autoConnect=false，SDK 发现 26 工具且 mock_status simulated=true，隔离测试的面板打开请求被拒绝。定向 7/7、全套 112/112、官方 plugin-creator validator 通过。没有执行实际个人安装/开始菜单点击/前台 GUI；不能把静态+无硬件验证当作正式安装或实机验收。步骤 9 做最终独立无硬件集成、打包与安全回归。

步骤 9 最终复核：主 Agent 串行再次执行隐藏完整测试 112/112，退出码 0；打包后 source/plugin 18 个关键文件 SHA256 相同，官方 plugin-creator validator 退出码 0，PowerShell 安装脚本解析 0 错、包版本 0.2.0，默认 MCP 命令指向 package-local mock launcher。最新包 SDK smoke 发现 26 工具，status 和 workspace_current 明确 simulated=true，隔离时 esp32_open_panel 不启动窗口；测试 broker 残留 0。已有个人安装副本仍为 0.1.0+codex.20260925172000，未覆盖，未重新加载用户 Codex 缓存、未创建个人快捷方式。全部测试不启动真实 bridge、串口、DSH 或可见 Tk。当前无硬件批准范围完成；后续真实安装/可见 UI 验收另行安排，但不属于本轮隐藏验收结果，实机试点仍须单独授权。
