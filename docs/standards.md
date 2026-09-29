# 当前面板技术规范

现有 Tk 窗口仍是运行基线。网页风格面板按 [专项计划](web-panel-plan.md) 分阶段开发；窗口宿主须复用既有单实例、命名管道、默认 mock 与确认门禁，页面不得直接取得 broker 令牌或提供任意 RPC 通道。UI-2 验证内联页面可在无网络监听下运行；UI-3 的页面 API 仅为受限只读 `get_snapshot()`，返回字段须白名单裁剪、长度有界、错误信息不泄漏内部路径或凭证。UI-4 至 UI-6 仍须按各自验收条件推进，不改变下列已生效规范。

既有设备安全规则见 [safety-and-design.md](safety-and-design.md)，桥协议见 [bridge-protocol.md](bridge-protocol.md)，MCP 工具合同见 [tools.md](tools.md)。这些文件继续有效；本文件只列本轮新增约束。

- Windows 本机 Tkinter 小窗口、单实例控制进程、命名管道 JSON 版本协议；不开放网络监听。一个 Windows 用户同时只管理一个活动工作区和设备。
- 窗口与所有 MCP 客户端共享桥状态和有界控制台缓冲。每个动作由后端串行执行；客户端断开、超时和异常必须释放其租约，最后客户端离开时释放所持桥。控制进程只管理自己创建的桥，不接管 DSH。
- 工作区切换仅在未连接设备时允许；冲突须明确报错。路径和 board.json 按既有规则验证；配置写入应避免部分文件。
- 默认 mock 并标记 `source`、`simulated`；真实模式仍须显式配置现有门禁。打开窗口不连接串口，也不改变安全策略。
- 窗口自身发起的受策略保护动作逐次显示目标及影响并取得真人确认。用户明确授权具体目标/效果范围并要求 Agent 操作面板时，Agent 可通过独立面板决策接口代办匹配的待确认操作；记录为 `agent_delegated`，不视为真人批准。真人确认门禁保持 fail-closed，模型参数不能充当真人批准或扩大授权。
- 下载、写入、删除与 PID 修改继续要求现有严格备份、capability、CRC/大小/语法校验。`generic` 停止只表示程序被中断；任何机型都不得声称实体运动已获确认。
- 不修改 `../dsh-plugin/` 或板载程序。未获单独授权，不运行真实桥、不枚举/打开串口、不连接或控制实体设备。
- 测试和日志只记录可复核证据，明确区分 mock、假桥和实机；不保存密钥、完整串口输出或用户隐私。

### 步骤 1 IPC 合同

- Windows 用户 SID 派生唯一命名管道及 Global mutex，以覆盖同一用户的多个登录会话；已运行的控制进程必须拒绝重复实例启动，不得抢占或结束原进程。管道 DACL 只授权当前 SID。
- 第一帧必须是严格 schema 的 hello，包含整数协议版本、随机 256 位令牌和请求的空闲期限。broker 在 hello 响应中回传实际协商期限；客户端以小于该期限的间隔发送只做租约续期的 ping，且与 RPC 共用请求锁。版本、认证信息、消息长度、JSON 类型或 schema 不匹配时 fail-closed；默认令牌保存在当前用户 LocalAppData 下，使用受保护 DACL，仅授权当前 Windows SID。
- 传输仅用 Windows 命名管道和 `send_bytes`/`recv_bytes` 风格的长度帧 JSON；帧头为 4 字节网络序长度，消息最大 1 MiB。禁止 pickle、网络监听和明文串口访问。
- 已认证连接各自持有 broker 租约；服务端按 hello 中协商的空闲期限（默认 60 秒，上限 600 秒）做有界等待，客户端读取等待也有界。EOF、协议错误、超时和异常断开都会关闭管道并由服务端 finally 释放租约；连接关闭不调用可能阻塞的 `FlushFileBuffers`。最后租约释放会关闭 broker 自己创建的桥；桥子进程放入 Windows `KILL_ON_JOB_CLOSE` job，以便 broker 崩溃/退出时自动清理。不能清理时不得静默接管其他进程。
- 步骤 1 RPC 仅提供只读 mock status；默认不得创建真实 bridge 或自动连接串口。MCP 路由与窗口接入留在后续步骤。

### 步骤 2 共享只读状态

- IPC 协议升为版本 2。严格 hello schema 在版本、256 位令牌、空闲期限之外，包含原 MCP 桥配置：mode、workspace、profile、bridge/mock 脚本、mock scenario 和真实控制/写工具开关。配置需通过枚举与类型校验；重叠租约带来不同配置时拒绝新客户端，所有租约结束且所属桥关闭后，允许下一组配置接入。
- broker 只允许 `status`、`ports`、`console` RPC；每项都经同一 broker 持有的 `BridgeClient` 串行执行。`console` 接受 `since` 与 `max_chars`，范围与原 MCP 方法一致，返回 broker 共享有界环形缓冲的 `text`、`cursor`、`dropped`。
- MCP `esp32_status`、`esp32_serial_ports`、`esp32_console_read` 输入和结果合同保持不变；`source` 与 `simulated` 仍由原 mode 标记。broker PID、桥 PID 等诊断字段不得泄露到 MCP 工具结果。
- 为保持尚未迁移到 broker 的既有控制/文件动作语义，如果当前 MCP 服务已启动本地 bridge，读取工具使用该服务的本地状态和控制台；没有本地 bridge 时通过共享 broker。步骤 3 完成动作迁移后再统一到共享状态。
- 默认 mock 必须使用 MCP 原有 `mock-scenario`、profile 和 workspace 配置；尤其 `fileops` fixture 不得替换成 `control` 或固定 `hiwonder`。真实模式只读桥沿用显式 `--mode bridge`、workspace、bridge-script 与既有只读命令门禁。本步不开放连接、控制、写入或删除 RPC。

### 步骤 3 共享控制动作

- `connect`、`disconnect`、`stop`、`run`、`interrupt`、`send` 由单实例 broker 串行调用，多个客户端共享连接状态及控制台缓冲。MCP 工具名、输入/输出字段、source/simulated、策略与人类确认 fail-closed 不变。
- broker 在创建/调用桥前复核真实控制 `allow_real_controls` 标志；真实 bridge 配置未显式启用控制时，直接 RPC 必须拒绝且不得启动桥。broker 内再次核对连接状态、connect 端口和确认等待期间共享控制 epoch。
- `hiwonder` stop 表示旧桥尝试双电机零速，不是硬件停转确认；`generic` stop 只中断程序。任何 MCP 结果都不得声称物理停转得到确认。
- 过渡例外：`--enable-write-tools` 的 MCP 服务继续在同一本地桥上执行文件、控制及读取，直到步骤 4 迁移文件 RPC。它持有 broker 排他本地桥租约；broker 不启动第二桥，其他客户端因配置冲突被拒绝，以避免双桥并发打开同一真实串口。该例外必须有回归测试，不能静默改变步骤 2 fileops 行为。
- bridge 子进程继续加入 broker 的 Windows Job，租约释放与最后客户端离开仍须清理资源；所有集成测试串行执行，禁止并行启动多个共享用户级 broker 的测试。

### 步骤 4 共享下载、文件与 PID

- 步骤 3 的 write-enabled 本地桥条款是历史过渡；当前 MCP 状态、控制与文件命令均由单实例 broker 唯一桥处理。`local_bridge_owner` 仅作旧客户端拒绝兼容字段；新客户端始终为 false。重叠租约后端配置冲突仍拒绝，不会产生第二桥。
- broker `file` RPC 只允许 capabilities/listfiles/readfile/download/writefile/deletefile；真实模式再次检查 allow_real_writes，mutation 要求严格备份与 capability；板载路径遵循原验证规则。命令与控制共享串行锁，文件读/目录读与 mutation 更新共享 epoch，旧请求 fail-closed。
- 单物理 JSON 帧仍限制 1 MiB；为保留 1 MiB 源文件与 raw Base64 响应支持，使用严格序号、哈希校验、每块 384 KiB 的分帧机制，逻辑消息上限 2 MiB。PIPE_NOWAIT 背压仅作有界重试，损坏与乱序均拒绝；客户端读等待覆盖旧桥文件命令上限。
- 真人确认渠道尚未完成验收；需要真人决定的生产操作继续 fail-closed。用户明确授权范围内的 Agent 代办走单独面板决策路径，必须记录来源且不能计入真人渠道验收。仅 mock/假桥验证，DSH strict 省略路径不修改。验收按文件清单/哈希及实际复测，不得称 Git clean；所有进程隐藏窗口运行。

### 步骤 5 共享工作区与策略

- broker 内部 workspace RPC 的 current/policy_get 为只读；claim/select/policy_set 要求唯一租约、共享 epoch 未变，且 broker 已持有的桥必须明确处于断开状态。连接中不得切换或修改策略；多客户端时必须先让其他客户端释放租约。select 先通过工作区检测、board 入口与 profile 验证；成功后关闭自己持有的已断开桥并更新配置/epoch，下一次桥读取按新工作区启动。
- claim 只新增不存在的 board.json；profile、label 和 .py 入口严格校验，目标已有则拒绝覆盖，fsync 临时文件并原子排他创建。策略保留 auto/confirm-write/confirm-all，版本哈希与读取值一致时才原子更新；无效旧策略不得静默覆盖，其他 JSON 字段予以保留。
- MCP 只新增只读 esp32_workspace_current；认领/切换/放宽策略的内部 RPC 供将来的面板逐次明确确认后调用，不把未验证的人类确认通道包装成 MCP 授权。
