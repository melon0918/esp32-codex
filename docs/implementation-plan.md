# 分步执行计划

每一阶段先写当日目标，完成交付物和验证后更新日志，再进入下一阶段。没有证据的项目保持“待办”。

| 阶段 | 本阶段只做什么 | 完成条件 |
| --- | --- | --- |
| 0. 规格基线 | 固定需求、架构、安全规范和日志机制 | 本目录文档齐全，用户已确认关键范围；不修改设备 |
| 1. 协议隔离 | 梳理 bridge.py JSON-lines 命令，建立模拟桥和协议夹具 | 无设备可验证命令/响应、超时、退出和错误路径 |
| 2. 只读 MCP | 实现工作区、端口、状态、控制台和板载只读工具 | MCP 客户端能发现工具；只读调用与模拟桥通过；占用报错清楚 |
| 3a. 控制与授权 | 连接、断开、停止、中断、运行、单行 REPL；建立服务端三档策略与用户确认边界 | mock 和无硬件真实桥夹具覆盖控制路径；真实工具默认未注册，显式启用后仍执行策略门禁；需确认操作在 Codex UI 确认通道验收前 fail-closed |
| 3b. 写入与调参 | 下载、板载文件写删、PID 修改；依赖经确认的备份失败即中止策略 | 覆盖前备份可证明成功；备份失败不发出覆盖；大小、CRC、语法校验失败路径通过；三档策略通过 |
| 4. Codex 包装 | 创建插件清单、技能、配置和安装说明 | 插件结构验证通过，Codex 新任务可发现工具；不依赖 DSH 运行 |
| 5. 实机试点 | 经用户同意，先只读连接，再分别批准控制和写板试验 | 结果、串口释放和 DSH 切换均有记录；问题有回退方案 |

## 当前阶段

- 阶段 1 已通过独立测试和负责人验收（2026-09-25，15/15 无硬件测试通过）。
- 阶段 2 已通过独立测试和负责人验收（2026-09-25，23/23 无硬件测试通过；官方 MCP SDK ClientSession 完成发现及七个工具调用）。真实设备、Codex 插件安装仍未验证。
- 阶段 3a 的 mock-only 切片已通过独立测试和负责人验收（2026-09-25，34/34 无硬件测试通过）。这只证明模拟控制、策略门禁和真实桥隔离。
- 用户现已明确授权实现真实桥连接、断开、停止、运行、中断及单行 REPL 的代码和无硬件测试；实体设备连接/控制测试不在授权范围内。本轮仍禁止启动 `../bridge.py` 或访问串口。
- 阶段 3a 控制工具和真实桥门禁已完成并通过独立/负责人测试；真实桥需显式 `--enable-control-tools`，需确认动作在 Codex UI 确认通道验证前 fail-closed。mock elicitation 只用于模拟测试。
- 六个通用控制工具与 canned JSON-lines 无硬件夹具已实现；独立 Luna MAX tester 和负责人复跑全套无硬件测试均为 40/40 通过，阶段 3a 控制代码验收通过（2026-09-25）。测试命令见 [tools.md](tools.md) 与 [tests/README.md](../tests/README.md)。
- 阶段 3b 当前授权实现下载、板载文件列表/读取/写入/删除和 PID 读取/修改；旧桥文件读取可能打断程序，调用影响需准确描述。
- 用户已决定任何覆盖或删除前必须先备份；不能可靠区分目标不存在与读取失败时 fail-closed。严格路径保存原始 bytes 到工作区根目录的独占备份文件，flush/fsync 后回读并校验 bytes/长度/CRC；全通过后才发送目标覆盖或删除命令。旧 DSH 不带 strict 参数时行为保持不变。
- 阶段 3b 当日目标和验收标准见 `devlog/2026-09-25.md`：工作区根目录 `.py` 下载按配置入口（hiwonder `/corex.py`，generic/四足 `/main.py`）；PID 使用同一入口。旧 bridge 不支持新 capability 时，写入、删除、下载和 PID 修改在任何 mutation 命令前拒绝。
- 阶段 3a 真实桥控制默认禁用：BridgeClient 只有在显式 `--enable-control-tools` 下才允许 `connect`、`disconnect`、`stop`、`run`、`interrupt`、`send`，否则白名单仍为 `ports`、`status`；`esp32_mock_*` 工具在真实模式下仍拒绝且不启动桥进程。真实桥板载文件 list/read 和写入工具另需 `--enable-write-tools`；后者还要求 `--enable-control-tools`。本轮不做设备试验。
- mock elicitation 的接受、拒绝和不支持分支仅是模拟协议测试；不得把模拟客户端接受或 SDK 返回 `accept` 当作生产人类授权已验证。
- 阶段 3b 的 MCP 工具、可选 strict bridge 扩展和无硬件回归已通过独立测试与负责人验收（2026-09-25，双方全套 53/53 通过）。`--enable-write-tools` 与 `--enable-control-tools` 分开控制注册，避免已有控制配置意外暴露写入工具；本阶段没有运行真实桥或设备。
- 阶段 4 目标（已完成）：在本项目中形成自包含、可复现的 Codex 个人插件包，提供清单、经本机验证的 MCP 配置、依赖安装说明和默认 mock 模式；真实桥代码可随包，但默认不启用。创建默认个人 marketplace 条目并本地安装，通过 Codex 新任务验证工具发现。
- 阶段 4 验收条件：使用官方 plugin-creator validator 验证插件结构；本机 Codex CLI 对 marketplace/install 流程无误；安装后 MCP 服务不依赖 DSH Desktop 路径或用户特定项目路径，默认启动 mock 桥；Codex 新任务能发现阶段 1–3b MCP 工具；记录 CLI、SDK 依赖及仍未验证的真实 UI 确认与设备行为。整个阶段不启动真实桥、不枚举串口或连接设备。
- 阶段 4 开发交付已完成并由负责人于 2026-09-26 验收通过。独立 Luna MAX tester 使用插件安装 venv 执行完整无硬件测试，53/53 通过；官方 plugin-creator validator 对源码包、profile 安装和 cache 均通过。安装配置上的 SDK ClientSession 发现 24 个 ESP32 工具，mock esp32_status 返回 source=mock_bridge、simulated=true、port=MOCK0。Codex CLI ephemeral 日志在修复启动参数后显示 esp32_status MCP tool call；本次 CLI 的 approval policy is never 拦截该调用，随后模型后端网络不可用，故仅将此作为发现证据，CLI 实际工具调用待可用网络/审批策略环境复验。测试进程无残留。阶段 4 不涉及真实桥、串口或设备。
- 当前 personal plugin cachebuster 版本为 0.1.0+codex.20260925172000；该版本用于同步负责人验收状态和阶段 5 待设备状态到 Codex cache。
- 当前阶段为阶段 5（实机试点），等待设备。Codex UI 人类确认通道和实机行为仍未验证，需确认的真实动作继续拒绝；开始实机试点前按阶段 5 顺序取得用户授权。

## 每阶段交付记录

在 ../devlog/YYYY-MM-DD.md 写：目标、完成事项、验证及结果、未解决问题、下一步。若改变需求或技术决定，同时更新对应 docs/ 文件，并在日志标注原因。
