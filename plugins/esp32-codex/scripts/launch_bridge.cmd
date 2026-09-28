@echo off
setlocal
for %%I in ("%~dp0..") do set "ESP32_PLUGIN_ROOT=%%~fI"
set "ESP32_PLUGIN_PYTHON=%ESP32_PLUGIN_ROOT%\.venv\Scripts\python.exe"
if not exist "%ESP32_PLUGIN_PYTHON%" (
  echo ESP32 Codex plugin venv is missing. Run scripts\install_personal_plugin.ps1 first. 1>&2
  exit /b 90
)
"%ESP32_PLUGIN_PYTHON%" -X utf8 -u "%ESP32_PLUGIN_ROOT%\mcp-server\server.py" --mode bridge --workspace "%CD%" --bridge-script "%ESP32_PLUGIN_ROOT%\bridge.py" --enable-control-tools --enable-write-tools
exit /b %errorlevel%
