# ESP32 Codex

Codex personal plugin for the ESP32 MCP server. The personal install starts the bundled real `bridge.py` in bridge mode and labels results with `source: "bridge_process"` and `simulated: false`. It enumerates serial ports but never connects automatically. `scripts\launch_mock.cmd` remains available as a mock fallback.

## Build and install

Run these commands from the project root in PowerShell. Use Python 3.10 or newer; the install script checks the selected interpreter, creates an isolated plugin `.venv`, and installs the pinned MCP SDK, pyserial, and WebView dependencies.

```powershell
$python = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = (Get-Command python -ErrorAction Stop).Source }
& $python .\scripts\build_plugin_package.py
& .\plugins\esp32-codex\scripts\install_personal_plugin.ps1 -PythonPath $python
$env:CODEX_HOME = Join-Path $env:USERPROFILE '.codex'
codex plugin add esp32-codex@personal
codex plugin list --json
```

The installer copies the package to `$env:USERPROFILE\plugins\esp32-codex` and rewrites that copy's `.mcp.json` to use the real launcher with the current ESP32 project as its working directory. The default personal marketplace is `$env:USERPROFILE\.agents\plugins\marketplace.json`; its `personal` entry points to `./plugins/esp32-codex`. Keep the installed source directory in place after installing the Codex cache copy. If you move or remove it, run the installer again and reinstall the plugin.

To update an installed package, use the plugin-creator cachebuster helper on the project copy, rerun the installer with `-Force`, then remove and add `esp32-codex@personal` with Codex CLI so the cache copy is refreshed. The installer itself checks the existing personal marketplace entry and does not rewrite marketplace metadata.

## Windows control panel (UI-9)

The current-user Start Menu shortcut **ESP32 Codex 控制面板** starts the compact WebView panel in real bridge mode. It lists available ports and waits for an explicit Connect action. The **ESP32 Codex 网页面板 MOCK** shortcut is the mock fallback, and **ESP32 Codex 控制面板（Tk 回退）** remains available for diagnostics. The Codex tool `esp32_open_panel` uses the same real web window and broker as the MCP tools; `ui="tk"` remains available. Panel confirmations authorize only the panel's current operation; MCP confirmation policy remains separate.

For no-popup validation, use `python -B -m panel.launcher --probe --mode bridge --workspace <absolute-workspace> --bridge-script <absolute-path-to-bridge.py> --enable-control-tools`: it reports `autoConnect=false`, `guiCreated=false` without creating a window or opening a serial port. Use the mock Start Menu shortcut for simulated development. The installer refuses to replace an existing package or Start Menu shortcut unless explicitly invoked with `-Force`.

The package includes `web-panel/host/` and `web-panel/prototype/`; the trusted WebView host inlines all page assets and exposes a fixed allowlist of shared-broker operations. The installer adds pinned WebView and pyserial dependencies and creates real web, mock web, and Tk fallback shortcuts. Updating an existing personal installation requires `-Force`. No local HTTP listener or serial auto-connect is used; user-profile installation is tracked separately from staged-package validation in `docs/web-panel-plan.md`.

## Runtime and tests

The plugin uses the official Python MCP SDK pinned in `requirements.txt`. The launcher runs the plugin-local `.venv\Scripts\python.exe`, so it does not depend on the DSH Desktop install directory or the project development environment. `pyserial==3.5` is installed from `requirements-bridge.txt` for the real bridge.

After installation, start a new Codex task in an ESP32 project and inspect `esp32_status`; verify `source=bridge_process` and `simulated=false`. The bundled tests can be run with the plugin venv:

```powershell
$python = Join-Path $env:USERPROFILE 'plugins\esp32-codex\.venv\Scripts\python.exe'
& $python -B -m unittest discover -s (Join-Path $env:USERPROFILE 'plugins\esp32-codex\tests') -v
```

The personal launcher passes `--mode bridge`, an explicit workspace and package-local bridge path, and registers control/write tools. The MCP server still fails closed for actions requiring an unverified client-side confirmation. Existing board-file writes require strict backup capability and an explicit panel confirmation. `launch_mock.cmd` enables only the simulator and is kept as a quick recovery route.

## Included files

- `mcp-server/`: MCP server, bridge client, policy and workspace logic.
- `tests/`: no-hardware JSON-lines simulator, canned responses and MCP tests.
- `bridge.py`: the real bridge source used by the personal launchers.
- `docs/`: project requirements, protocol and safety documentation.

### Codex 后台运行于 WSL
在 Windows PowerShell 调用个人安装脚本时增加 `-McpHost Wsl`（更新已有安装同时用 `-Force`）。它使用默认 WSL 发行版的 wslpath 转换 command/cwd，保留 Windows launcher 参数与 Windows 串口桥。原生 Windows 后台使用 `-McpHost Windows`（默认）。随后按本地插件 cachebuster 流程重新安装；仅重启客户端无法修复错误路径。
