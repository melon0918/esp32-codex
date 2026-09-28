# 无硬件测试

## 阶段 1：JSON-lines 协议

这组夹具只覆盖 JSON-lines 传输封套，不模拟 MicroPython REPL、板载文件系统或串口设备行为。模拟桥使用 Python 标准库，不导入 `../bridge.py` 或 pyserial，不会枚举、打开或写入串口。

在 codex-plugin 目录运行。Codex 桌面配置的 Python 解释器可通过以下 PowerShell 命令调用，不依赖 `py` 启动器或额外环境变量：

```powershell
$python = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
& $python -B -m unittest discover -s tests -p "test_protocol_simulator.py" -v
```

若 Python 3 已在 PATH 中，也可把 `$python` 设为 `python`。

每个 bridge 命令都有独立的请求和 canned 响应 fixture。用例覆盖启动日志、各命令响应形状、命令错误、坏 JSON、有效非对象 JSON、空行、缺失 id、多个 id 的顺序、事件与响应交错、download 校验失败、模拟超时和进程退出。`stop.response.jsonl` 忠实记录旧 wire 响应；generic 的停止动作仅中断程序，不能据此认定电机或舵机已安全停下。

## 阶段 2：MCP 客户端和模拟桥

阶段 2 使用项目固定的官方 MCP Python SDK。先安装 SDK，再用 Python SDK 的 stdio `ClientSession` 发现并调用工具；该测试只连接 `tests/mock_bridge.py --scenario readonly`，没有硬件依赖。

```powershell
$python = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
& $python -m pip install -r requirements.txt
& $python -B -m unittest discover -s tests -p "test_readonly_mcp.py" -v
```

SDK 已安装后可一次运行两组：

```powershell
& $python -B -m unittest discover -s tests -v
```

阶段 2 的模拟桥不需要 `pyserial`；真实桥模式另需 `requirements-bridge.txt`，开发与无硬件测试不得启动真实 `../bridge.py`。此处 MCP SDK 客户端测试验证 stdio 初始化、工具发现与工具调用；Codex 插件市场安装和包装仍属阶段 4。

## 阶段 3b：严格备份和写入工具

MCP 文件工具只在 `--enable-write-tools` 下注册；模拟模式使用 `--mock-scenario fileops`，无需设备或串口。真实桥模式还要求 `--enable-control-tools`。以下用例通过 MCP ClientSession、状态ful 模拟桥和直接加载但不启动的 bridge.py 单元夹具覆盖工具发现、下载源路径与 SHA 复核、文件写删、PID、capability 缺失，以及严格备份失败门禁：

```powershell
& $python -B -m unittest discover -s tests -p "test_write_tools_mcp.py" -v
& $python -B -m unittest discover -s tests -p "test_strict_backup_bridge.py" -v
```

`test_strict_backup_bridge.py` 用假的 `serial` 模块导入桥类，不调用 `serial.Serial`、不运行桥主循环。它断言目录枚举、原始读取/CRC、备份目录写入或回读失败时未发送目标 `open(...,'wb')` 或 `os.remove`，并检查 DSH 不传 `strictBackup` 时仍沿用旧路径。完整复测由独立 tester 执行；开发 agent 本轮不运行测试。

## 阶段 3a：模拟控制与策略分支

该组只通过官方 MCP SDK `ClientSession` 连接 `tests/mock_bridge.py --scenario control`。它检查 mock-only 工具发现、模拟连接/断开/运行/中断/停止、REPL 输入只记录不执行、三档策略的确认分支、拒绝或不支持时 fail-closed，以及真实桥模式下 mock-only 工具在启动桥前被拒绝。

```powershell
& $python -B -m unittest discover -s tests -p "test_mock_control_mcp.py" -v
```

需确认分支时，测试客户端通过 SDK elicitation callback 返回接受/拒绝；这只验证模拟协议流程，不验证 Codex UI 是否提供可信的人类确认。

显式控制工具注册与真实桥命令门禁由 canned 子进程夹具验证：

```powershell
& $python -B -m unittest discover -s tests -p "test_control_tools_mcp.py" -v
```

该夹具不导入或启动 `../bridge.py`，不枚举串口，也不连接或操作实体设备。它确认默认不注册控制工具、`--enable-control-tools` 显式开关、已连接时重复 connect 的响应，以及策略确认前真实控制命令不会发出。`confirm-all` 的控制与 `confirm-write`/`confirm-all` 的 REPL 在生产桥模式仍 fail-closed，直到 Codex UI 人类确认通道完成验证。真实板载文件工具现另需 `--enable-write-tools` 与 `--enable-control-tools`，并按当前策略门禁执行。

## 当前面板步骤 4：共享文件/PID 无硬件回归

`tests.test_broker_protocol` 验证两套独立工具实例使用唯一 broker 桥共享文件写删、下载、PID、控制台与 epoch；直接 file RPC 严格备份/真实开关/陈旧 epoch 拒绝；1 MiB UTF-8 内容与 raw Base64 响应通过不超过 1 MiB 的独立帧分块、SHA-256 与序号校验。无真实设备、串口或前台窗口。原 `test_write_tools_mcp`、`test_strict_backup_bridge` 同步参与全套回归，保护 DSH strict 省略时的旧行为。后续执行通过隐藏窗口子进程，在工作区根目录运行 `-B -m unittest discover -s tests -q`，共享单实例集成测试禁止并行。

## 当前面板步骤 5 工作区/策略无硬件回归

`tests.test_workspace_control` 验证 board.json 排他认领、入参校验、策略原子写入及旧哈希拒绝；`tests.test_broker_protocol` 验证唯一租约切换、工作区来源共享、已连接时拒绝以及多客户端保护；`tests.test_readonly_mcp` 验证只读 `esp32_workspace_current`。测试都走 mock/假桥、隐藏进程，不启动 Tk 窗口。
