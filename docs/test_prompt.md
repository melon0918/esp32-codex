# 独立验收提示

UI-10 真机版最终验收（2026-09-28）：个人插件 `esp32-codex@personal` 通过 Codex CLI 安装并启用；安装配置使用真实 `launch_bridge.cmd`，工作目录指向 ESP32 IDE 根目录，固定安装 pyserial 3.5。默认开始菜单入口启动真实 Web 面板，MOCK Web 与 Tk 回退仍在；安装包 MCP `esp32_open_panel(ui="web")` 显示真实来源且 `autoConnect=false`，真实模式 `--probe` 不建 GUI、不连串口。真实 MCP ClientSession 发现 26 个工具；COM4/CH340 返回 `source=bridge_process`、`simulated=false`，连接识别 MicroPython v1.24.1，断开后 `connected=false`。实际桥选择 `hiwonder` profile，断开响应 `physicalStopConfirmed=false`，不可将其表述为物理停止已确认。未运行程序、REPL 或板载文件操作。完整测试 114/114、launcher 定向 9/9、官方插件 validator 和缓存/个人安装 12 项关键哈希通过。CLI 原 `personal` marketplace 配置错误指向 WSL `个人 WSL 用户目录`，经 CLI 移除错误 override 后恢复默认个人 marketplace 并成功刷新插件。真实窗口进程已启动，但当前 WSL Computer Use 无法采集截图；本阶段不作为像素级目视验收。完整证据见 `../devlog/2026-09-28.md`。

UI-9 最新无硬件个人安装验收：个人插件版本 `0.2.0+codex.20260926151926` 已安装，原 profile 完整备份可用且 marketplace 文件未改。Web 主快捷方式和 Tk 回退快捷方式参数正确；默认 Web 与 Tk 回退窗口都实际打开、WM_CLOSE 后退出码 0。WebView2 截图 `../web-panel/review/ui9-personal-default.png` 为 874×929、DPI 192；无原生标题栏，中文清晰，MOCK 标识可见。两入口 `--probe` 均报告 `mode=mock`、`autoConnect=false`、`guiCreated=false`；安装包 MCP 隔离测试发现 `esp32_open_panel` 默认 `ui=web` 并拒绝测试夹具打开 UI。定向 launcher 9/9、完整 mock/假桥测试 114/114、官方 plugin validator 通过；源码、包、profile 13 项哈希匹配。安装脚本添加 UTF-8 BOM 后解决 Windows PowerShell 5.1 中文快捷方式路径误读，并完成重装。Codex CLI 不在可用 PATH/安装位置，尚未刷新 CLI 插件缓存；现有 MCP 服务未重启，需 Codex 新线程/重载后加载新工具默认值。没有启动真实 bridge、使用串口/ESP32 或修改 DSH。

UI-6 可见 Windows MOCK 实测（历史验收）：`verify_visible_ui6.py` 启动真实前台 WebView2 与隔离模拟 broker，在 200% 系统 DPI 下测试主界面、实际操作区、确认/拒绝、错误/共享控制台、700×700、页面 125%/150% 缩放；4 次实际系统鼠标点击，mock 严格备份下载成功，生成 8 张 `../web-panel/review/ui6-visible-*.png` 与 `ui6-visible-result.json`，正常关闭退出码 0。`verify_front_entries_ui6.ps1` 从 Temp 暂存包建立临时快捷方式，重复点击不新增窗口，开发 MCP `esp32_open_panel(ui="web")` 恢复已最小化的相同 PID/HWND 并获得前台焦点；`ui6-entries-result.json` 和 `ui6-visible-entry.png` 证据已复核；该阶段个人安装未覆盖。前台失败项修复：标题编码/截图 DPI 虚拟化改为句柄 PrintWindow；根据工作区域适配尺寸；操作区初始化不再漏过 `pywebviewready`。最终 `verify_host.py` 9/9、`verify_actions.py` 3/3、`verify_readonly_broker.py` 2/2、项目完整 114/114、Edge/CDP 25 项/9 图均通过。仅确认 mock/暂存包，不代表实体 ESP32 或真实桥；UI-9 个人安装切换见上方最新验收。


网页风格面板后续逐阶段按 [专项计划](web-panel-plan.md) 验收。UI-1 已有实际尺寸及窄视口截图；UI-2 已证明本地页面无网络监听、页面不持有 broker 凭证、宿主依赖可独立安装和临时打包。UI-3 已验证只读共享；UI-4 至 UI-6 分别验证面板自身逐次确认、双入口单实例与无硬件回归。任何视觉或 mock 测试都不等于实体设备验收。

UI-1 证据：Windows Chrome 以 `file://` 打开静态稿，对 700×700 及相当于 125%/150% 缩放的 560/467px 窄视口截图；主视图、设置页、控制台、运行/下载确认、错误和忙碌状态均已目视复核。浏览器报告这些宽度 `scrollWidth=clientWidth`，设置页可纵向滚动；无页面脚本异常或外部请求。首轮 8–10px 低对比文字和首帧淡入问题已修订；策略样例改为默认 `confirm-write`。用户确认可继续开发后，UI-1 验收。

UI-2 主 Agent 独立证据：Windows Python 3.12.14 隔离 venv 中 pywebview 6.2.1 加载本机 WebView2 Runtime 154.0.4258.37；可见 700×700 窗口截图 `../web-panel/review/ui2-ping-final.png` 显示启动 ping、按钮 ping 和 Python 推送。退出报告中 `pageToPythonPingCount=2`、`lastPingKind=button`、`pythonToPageStateSent=true`、`processReturnedAfterWindowClosed=true`。宿主 PID 运行时 TCP Listen 数 0，窗口关闭后进程退出。未装 pywebview 的现有插件 venv 执行 `--probe` 返回码 2、`guiCreated=false`。隔离依赖 site-packages 为 19,240,754 字节；7 文件临时资源包 ZIP 20,950 字节，复制到 Windows Temp 后可独立 `--probe` 与可见启动、收到启动 ping/推送并关闭。源码 `web-panel/host/` 没有导入 PanelBackend/broker，也未连接设备；正式安装包和双入口留待 UI-5。

UI-3 主 Agent 独立证据：Windows 插件 Python 执行 `web-panel/host/verify_readonly_broker.py` 2/2 通过，默认 fileops 场景下网页 API 与 peer 从同一 broker 读到 MOCK0 状态和相同控制台；关闭网页后 peer 仍可读；normal 场景显示空工作区与未连接。`verify_host.py` 7/7 通过，覆盖字段白名单、12,000 字符控制台上限、长路径裁剪、固定错误提示及只读页面入口。实际 Windows WebView2 窗口截图 `../web-panel/review/ui3-empty-disconnected.png` 显示工作区、策略、状态、串口、空控制台和明显 MOCK 来源；静态 UI-1 演示默认折叠。Edge headless 520px 视口将约 1,100 字符工作区路径注入快照卡，`scrollWidth=503`、未超过 viewport。窗口关闭退出码 0；测试身份 broker 进程残留 0。UI-3 只支持 mock，不含操作按钮接线或正式安装。

UI-6 隐藏视觉与交互增量证据：独立 WebView2 隐藏窗口（hidden=true、focus=false）在模拟 broker 上完成页面加载、MOCK0 状态、真实网页下载确认、取消、重新批准及严格备份模拟成功；Edge headless/CDP 从当前宿主生成的内联页面渲染 9 张 PNG，25 项检查覆盖动作、确认、拒绝、错误、设置与控制台导航、700/560/467px 页面无横向溢出及仅本地资源。结果在 `../web-panel/review/ui6-headless-result.json`、截图 `../web-panel/review/ui6-headless-*.png`；该 CDP 页面使用独立注入 mock API，不等于 broker 实际交互，实际 broker 则由隐藏 WebView2 另行验证。最后 host 8/8、mock actions 3/3、全套 114/114 通过。可见原生窗口人工验收、实际个人安装、开始菜单/MCP 双入口真实聚焦和默认 UI 切换仍未验证。

UI-4/UI-5 本轮无硬件证据：`verify_host.py` 8/8、`verify_actions.py` 3/3、`test_panel_launcher.py` 9/9、全套 114/114，Windows 后台隐藏且退出码均 0；MCP 测试隔离状态下拒绝 Tk/web 启动，`--ui web --probe` 不创建 GUI、不自动连接。暂存包 6 份关键源/包哈希一致；隔离 venv 安装 pywebview 6.2.1 和固定 MCP SDK 成功，包内宿主 `--probe` 无 HTTP 服务且不创建 GUI。UI-6 Edge headless 退出 0 但未出截图，此项不得验收；用户禁止前台弹窗，所以尚无真正 WebView2 可操作窗口、可见确认/错误态、双入口聚焦或真实个人安装证据。Tk 默认与回退未切换。

供主 Agent 或独立验收 Agent 按当前步骤使用；不得把历史日志中的测试结果当作本轮证据。

1. 阅读 AGENTS.md、docs/plan.md、docs/requirements.md、docs/architecture.md、docs/standards.md、docs/safety-and-design.md、docs/status.md 和当天 devlog。只审查当前步骤的合同与实际修改范围。
2. 当前目录无 Git 根；用步骤前后的文件清单、哈希、逐文件对比和测试输出检查实际改动，不声称 Git 工作区干净。
3. 逐项核对默认 mock、真实模式显式门禁、DSH 串口不抢占、共享单连接、工作区冲突、服务端确认门禁、严格备份、`generic` 停止语义及进程资源清理。
4. 运行当前步骤指定的无硬件测试；必要时使用已安装插件 venv 执行 `-B -m unittest discover -s tests -q`。报告命令、退出码、用例数量和环境，不能用 mock 结果称实机通过。
5. 面板步骤需在可交互 Windows 会话验证窗口显示、两种入口、重复打开、关闭后的连接归属及确认对话框。缺少 GUI 或设备时明确标记未验证，不降低验收标准。
6. 报告可复现问题和文件位置。只有主 Agent 核对实现、测试和文档后才标记步骤完成。

步骤 1 已验收证据：个人插件 venv 执行 `python -B -m unittest tests.test_broker_protocol -q`，8/8 通过；随后全套 `python -B -m unittest discover -s tests -q`，61/61 通过。进程检查未发现残留 broker/mock。步骤 2 须重新检查双 MCP 客户端的只读状态、串口和控制台共享，保持既有工具名/输入输出及来源标记，不用本条历史结果替代验收。

步骤 2 已验收证据：主 Agent 串行执行 `python -B -m unittest tests.test_broker_protocol tests.test_readonly_mcp tests.test_bridge_silent_spawn -q`，22/22 通过；`python -B -m unittest discover -s tests -q`，67/67 通过，均无 skip。首次并行测试发生单实例冲突，串行重跑通过；后续不得并行运行共享 broker 的集成测试。步骤 3 应检验共享控制动作、MCP 服务端确认门禁、机型停止语义以及异常释放，禁止使用 mock 结果宣称实机通过。

步骤 3 恢复复核：44 项定向与 72 项全套虽均通过，但两个独立 MCP 客户端共享 epoch 的准备操作有未覆盖缺陷。`prepare_connected_action()` 取本地 `self._control_epoch`，在共享 client epoch=7 时返回 0，暂不验收。下一次窄修复须补测跨客户端顺序操作成功和确认等待期间旧 epoch 拒绝；保留 fileops 过渡、机型语义及安全门禁。验证一律隐藏窗口后台串行执行，禁止弹出 Tk 窗口或访问实体设备。

步骤 3 后续修复验收：从共享 client 获取 epoch；两套独立 `Esp32McpTools`/broker facades 的连接后跨客户端运行、停止、中断、REPL 以及旧 epoch 拒绝新增用例通过 1/1，隐藏运行全套 73/73 通过、退出码 0。仅 mock/假桥，步骤 3 通过。步骤 4 须重点检查 broker 文件 RPC 与控制共用单桥、严格备份/capability/CRC/语法门禁及文件/控制资源串行，并验证现有 DSH 默认路径兼容。

步骤 4 主 Agent 验收：新增同桥文件读写删、download、PID、epoch 过期拒绝、真实开关严格备份拒绝、1 MiB 大文件分帧与 SHA/序号校验等无硬件测试。边界首轮暴露 PIPE_NOWAIT 背压误判，修复后边界 2/2、全套 78/78 通过且退出码 0，测试 broker 无残留；现有 DSH strict 省略兼容用例通过。mock/假桥不能当作实机验证。步骤 5 需检查工作区认领与切换在已连接、不同租约和异常条件下拒绝，策略读写及 board.json 合同保持原有来源与安全性。

步骤 5 主 Agent 验收：新增本地认领/策略四项及 broker 单租约、已连接拒绝两项，另有 MCP 只读共享工作区用例；最终全套 85/85 通过。原始测试进程曾因共享生产 broker 导致跨套件配置冲突，SDK stdio 不会转发任意环境变量，故各测试 helper 显式传入隐藏 --test-broker-seed：生产默认不受影响，真实模式仅接受临时 fake bridge；测试身份无残留。步骤 6 必须验证 mock 状态、端口、console、来源、策略和关闭不接管桥；用户禁止前台弹窗，务必通过隐藏/离屏 Tk 测试，不能用虚构可见检查代替证据。

步骤 6 主 Agent 验收：新增关闭面板不影响另一客户端共享桥租约的测试，hidden Tk 字段断言，定向 6/6、全套 93/93 通过，退出码 0，无测试 broker 残留；前台窗口的真实视觉体验未测试。步骤 7 须仅在隐藏/离屏环境验证面板自身逐次确认、拒绝/取消不执行、三档策略和 Codex 对话侧门禁隔离，不能弹出前台窗口。

步骤 7 主 Agent 验收：隐藏定向 14/14、全套 105/105 通过，测试 broker 无残留。实际私有 mock broker 验证拒绝连接/运行不发送、确认下载严格备份，UI 只读工作区识别不选择且切换后入口/策略同步。前台真实人工交互未验，步骤 8–9 的安装/双入口必须隐藏执行，不覆盖用户现有安装副本。

步骤 8 无硬件验收：Windows per-user mutex、pythonw 请求、probe 不创建 Tk、MCP 工具发现及测试隔离拒绝弹窗、暂存目录打包、开始菜单脚本解析与 mock 配置；定向 7/7、全套 112/112，官方 validator 通过，SDK 包内发现 26 个工具且 simulated=true。实际 profile 安装/创建快捷方式/前台操作禁止执行，仍标记未验。步骤 9 须独立重验全套、包内源码哈希和官方结构、真实模式不自动启动、设备/DSH 无改动、测试进程清理；不可把前台或实机标记通过。

步骤 9 最终无硬件验收：再次串行隐藏全套 112/112 通过，退出码 0；插件源码包与原项目 18 个关键源码/测试/文档文件 SHA256 同步，官方 validator 0、打包 MCP SDK 发现 26 工具，来源 mock_bridge 且 simulated=true，隔离环境不会调用窗口。测试 broker 残留 0，用户 profile 安装仍保留旧版且未覆写。步骤 0–9 已完成规定无硬件范围；可见 UI/快捷方式实际点击、Codex 生产人类确认及实机没有验收，不得依赖模拟结果宣称它们通过。步骤 10 于 2026-09-26 获授权；用户当前未连接设备。首轮可见 mock 面板字符偏小且发虚，主 Agent 用 Windows Pillow 实际截图确认；字体改为 Microsoft YaHei UI 且正文增大后，再次截图目视未见发虚或裁切。Windows 隐藏面板定向测试 21/21 通过，空工作区和长路径的布局请求 696×683，不超出 700×700 窗口。控制和写板仍须分别授权。
