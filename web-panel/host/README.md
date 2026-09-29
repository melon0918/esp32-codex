# UI-2 至 UI-4 Windows 窗口宿主

`host.py --demo` 是独立的 UI-2 pywebview 技术验证，不接 PanelBackend、broker、桥进程、串口或 ESP32。`host.py --readonly` 是 UI-3 只读页面，复用 `mcp-server/panel/backend.py`，通过现有单实例 broker 获取 mock 状态。两条路径都从 `../prototype/` 内联 HTML、CSS 和 JavaScript 到内存页面，通过 `webview.start(http_server=False)` 明确关闭 pywebview HTTP 服务。

UI-2 窗口标题为 **`ESP32 Codex 网页面板 · UI-2`**。唯一暴露的 Python 方法是 `ping(kind)`，仅接受固定的 `startup` / `button` 标签并返回示例 pong；宿主还会推送固定的“宿主就绪”状态。

UI-3 窗口标题为 **`ESP32 Codex 网页面板 · 只读 MOCK`**。页面唯一 API 是无参数 `get_snapshot()`，宿主只映射工作区、策略、来源、串口、状态和最多 12,000 字符的控制台文本；字段经 allowlist 与长度裁剪后以 `textContent` 写入。页面不持有 broker token、管道句柄或任意 RPC。异常仅返回固定提示，不转发异常详情、路径或传输诊断。启动不会发送 connect，关闭只释放此窗口的 broker 租约。UI-1 原页面默认折叠在“静态界面演示”区域，明确标注其虚构内容与实时后端状态隔离。

## 依赖

- Windows 10/11 与本机 Edge WebView2 Runtime。准备记录中的机器版本为 `154.0.4258.37`；`--probe` 会只读列出当前机器上发现的运行时目录。宿主优先读取标准安装目录环境变量；MCP 宿主缺少这些变量时按系统盘回退到标准安装目录。
- Python 3.10 或更新版本。
- `requirements.txt` 固定 pywebview 及本次 Windows 运行所需的传递依赖版本。只在临时隔离 venv 安装，不向插件 venv 或当前 Python 添加依赖。
- 本机 Python 3.12.14 隔离安装后，site-packages 合计 **19,240,754 字节（18.35 MiB）**；此数值不含 Python 运行时或系统 WebView2 Runtime，也不是最终打包产物体积。

pywebview 官方说明 `create_window(html=...)` 可在无 HTTP 服务时加载内存页面；相对本地路径会自动启动 HTTP 服务。该原型因此将原型静态资源内联，并在 `start` 时明确传入 `http_server=False`。[官方 API](https://pywebview.flowrl.com/api/) · [官方无服务架构说明](https://pywebview.flowrl.com/guide/architecture)

## 隔离安装与命令

以下示例在 PowerShell 执行。将 `<repo>` 换成开发目录的 Windows 路径；创建新的临时 venv，不要复用或改写插件 venv：

```powershell
$venv = Join-Path $env:TEMP 'esp32-ui2-pywebview-venv'
py -3.12 -m venv $venv
& "$venv\Scripts\python.exe" -m pip install -r '<repo>\web-panel\host\requirements.txt'
& "$venv\Scripts\python.exe" '<repo>\web-panel\host\host.py' --probe
& "$venv\Scripts\python.exe" '<repo>\web-panel\host\host.py' --demo --exit-report (Join-Path $env:TEMP 'esp32-ui2-exit-report.json')
```

`--probe` 只检查当前系统、Python、pywebview 包和 WebView2 Runtime；报告 `httpServer=false`、`guiCreated=false`，不创建窗口。依赖缺失时返回码为 2。`--demo` 打开可见 700×700 UI-2 窗口；`--readonly` 打开读取现有 PanelBackend 的 UI-3 窗口，默认 `--mock-scenario fileops --profile generic`。`--workspace`、`--profile` 和 `--mock-scenario` 必须与已运行的 MCP/broker 配置一致，否则现有 broker 会按既有策略拒绝不匹配的租约。不得向此入口传入真实 bridge 参数；其 CLI 不支持真实模式。

UI-3 运行时需要能导入本项目 `mcp-server` 依赖的 Python 环境，并在同一环境安装本目录声明的 pywebview 依赖。独立 demo venv 可单独用于 UI-2；UI-5 再负责产品依赖打包。

## UI-2 手动联调

1. 在页面底部找到“窗口宿主联调”状态卡，等待 `Python 已推送：宿主就绪 · MOCK`。
2. 检查“页面 → Python”显示 `pong · startup · #1 · MOCK`。
3. 点“发送 ping”，计数应增加；操作只更新内存中的演示状态。
4. 关闭窗口，确认 Python 进程退出并检查 exit report 中的 `processReturnedAfterWindowClosed=true`。

## UI-3 无硬件验证

```powershell
& '<repo>\plugins\esp32-codex\.venv\Scripts\python.exe' -B '<repo>\web-panel\host\verify_host.py'
& '<repo>\plugins\esp32-codex\.venv\Scripts\python.exe' -B '<repo>\web-panel\host\verify_readonly_broker.py'
```

共享 broker 验证使用隔离测试 namespace、同一 fileops mock 配置和两个 named-pipe 客户端，比较状态/控制台，关闭页面客户端后再由 peer 读取状态。另一个 `normal` 场景检查空工作区和未连接显示。测试不启动 `bridge.py`、pyserial、实体 ESP32 或串口。所有 broker 子进程由验证脚本等待并在超时后结束。

UI-6 可复现无前台像素检查：Windows Node 24 执行 `node web-panel/host/verify_visual_cdp.js`，使用已安装 Edge 的隔离无头 CDP 会话，页面 API 在 `ui6_browser_mock.js` 中模拟；9 张 PNG 与 25 项断言结果写入 `web-panel/review/`。Windows 隐藏 pywebview/WebView2 另用模拟 broker 验证实际宿主页面调用。用户授权前台窗口后，可用独立临时 venv 执行 `python -B web-panel/host/verify_visible_ui6.py` 进行真实前台 WebView2 + 原生鼠标/窗口截图验证，结果 `ui6-visible-result.json`、8 张 `ui6-visible-*.png`；`powershell -File web-panel/host/verify_front_entries_ui6.ps1` 只创建 Temp 快捷方式并从暂存包经开发 MCP 验证同一窗口聚焦，结果 `ui6-entries-result.json`。均只用模拟桥，不安装/覆盖个人插件。

UI-3 `--readonly` 仅装载只读快照；UI-4 `--operations` 另装载固定白名单 mock 操作与逐次确认。UI-8 的产品紧凑页面由 `panel.launcher --ui web --mode mock` 打开，内联 `compact.html`、`compact.css`、`compact.js` 和 `compact_live.js`，页面只访问继承自 `PanelOperationsApi` 的有限方法。窗口动作只额外暴露自身的最小化和关闭；工作区发现、确认处理和业务动作均由宿主固定参数复核。启动不连接串口，只有 mock 来源可启用操作；数据通过 `textContent` 写入，页内资源不产生网络请求。

`verify_host.py` 检查 UI-8 页面资源内联、唯一 ID、有限 API、无 `fetch`/WebSocket 和窗口大小；`verify_actions.py` 在假窗口及 mock broker 下验证拒绝/批准、跨客户端 epoch、REPL、工作区、策略、严格备份下载与紧凑窗口控制。UI-8 真实 WebView2 快照截图为 `../review/ui8-live-compact.png`。UI-9 个人安装默认入口切换已验收，最新个人安装 Web MOCK 截图为 `../review/ui9-personal-default.png`；Tk 显式回退。Codex CLI 缓存刷新与真实设备/串口仍未验证。
