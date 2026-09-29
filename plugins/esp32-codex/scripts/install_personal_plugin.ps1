[CmdletBinding()]
param(
    [string]$PythonPath,
    [ValidateSet('Windows', 'Wsl')]
    [string]$McpHost = 'Windows',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

function Invoke-LocalProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $quotedArguments = foreach ($argument in $Arguments) {
        if ($argument -match '[\s"]') { '"' + ($argument -replace '"', '\"') + '"' }
        else { $argument }
    }
    $prefix = Join-Path $env:TEMP ('esp32-install-' + [Guid]::NewGuid().ToString('N'))
    $stdoutPath = "$prefix.out"
    $stderrPath = "$prefix.err"
    try {
        $process = Start-Process -FilePath $FilePath -ArgumentList ($quotedArguments -join ' ') `
            -PassThru -Wait -NoNewWindow -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
        $stdout = if (Test-Path -LiteralPath $stdoutPath) {
            Get-Content -LiteralPath $stdoutPath -Raw -Encoding UTF8
        } else { '' }
        $stderr = if (Test-Path -LiteralPath $stderrPath) {
            Get-Content -LiteralPath $stderrPath -Raw -Encoding UTF8
        } else { '' }
        return [pscustomobject]@{ ExitCode = $process.ExitCode; Stdout = $stdout; Stderr = $stderr }
    } finally {
        Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
    }
}

$pluginSource = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$profileRoot = [Environment]::GetFolderPath('UserProfile')
if ([string]::IsNullOrWhiteSpace($profileRoot)) {
    throw 'Could not resolve the Windows user profile directory.'
}
$installRoot = Join-Path $profileRoot 'plugins\esp32-codex'
$deviceWorkspace = (Resolve-Path (Join-Path $projectRoot '..')).Path
$marketplacePath = Join-Path $profileRoot '.agents\plugins\marketplace.json'
$programsPath = [Environment]::GetFolderPath('Programs')
if ([string]::IsNullOrWhiteSpace($programsPath)) {
    throw 'Could not resolve the current user Start Menu Programs directory.'
}
$shortcutPath = Join-Path $programsPath 'ESP32 Codex 控制面板.lnk'
$mockWebShortcutPath = Join-Path $programsPath 'ESP32 Codex 网页面板 MOCK.lnk'
$tkFallbackShortcutPath = Join-Path $programsPath 'ESP32 Codex 控制面板（Tk 回退）.lnk'

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $pythonCommand = Get-Command python -ErrorAction Stop
    $PythonPath = $pythonCommand.Source
}
$pythonPathResolved = (Resolve-Path $PythonPath).Path
$pythonCheck = Invoke-LocalProcess -FilePath $pythonPathResolved -Arguments @(
    '-c', 'import sys; print("%d.%d" % sys.version_info[:2])'
)
if ($pythonCheck.ExitCode -ne 0) {
    throw "Could not run Python interpreter: $pythonPathResolved $($pythonCheck.Stderr.Trim())"
}
$versionParts = $pythonCheck.Stdout.Trim().Split('.')
if ([int]$versionParts[0] -lt 3 -or ([int]$versionParts[0] -eq 3 -and [int]$versionParts[1] -lt 10)) {
    throw "Python 3.10 or newer is required; found $($pythonCheck.Stdout.Trim())"
}
if (-not (Test-Path -LiteralPath $marketplacePath -PathType Leaf)) {
    throw "Personal marketplace was not scaffolded: $marketplacePath"
}
$marketplace = Get-Content -LiteralPath $marketplacePath -Raw | ConvertFrom-Json
$entry = @($marketplace.plugins | Where-Object { $_.name -eq 'esp32-codex' }) | Select-Object -First 1
if ($marketplace.name -ne 'personal' -or -not $entry -or $entry.source.path -ne './plugins/esp32-codex') {
    throw 'The default personal marketplace must contain esp32-codex at ./plugins/esp32-codex.'
}
if ((Test-Path -LiteralPath $installRoot) -and -not $Force) {
    throw "Install target already exists. Re-run with -Force to update: $installRoot"
}
if ((Test-Path -LiteralPath $shortcutPath) -and -not $Force) {
    throw "Start Menu shortcut already exists; refusing to overwrite: $shortcutPath"
}
if ((Test-Path -LiteralPath $mockWebShortcutPath) -and -not $Force) {
    throw "Start Menu shortcut already exists; refusing to overwrite: $mockWebShortcutPath"
}
if ((Test-Path -LiteralPath $tkFallbackShortcutPath) -and -not $Force) {
    throw "Start Menu shortcut already exists; refusing to overwrite: $tkFallbackShortcutPath"
}

$buildResult = Invoke-LocalProcess -FilePath $pythonPathResolved -Arguments @(
    (Join-Path $projectRoot 'scripts\build_plugin_package.py'), '--plugin-root', $pluginSource
)
if ($buildResult.ExitCode -ne 0) {
    throw "Plugin package build failed. $($buildResult.Stderr.Trim())"
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $installRoot) | Out-Null
New-Item -ItemType Directory -Force -Path $installRoot | Out-Null
Copy-Item -Path (Join-Path $pluginSource '*') -Destination $installRoot -Recurse -Force

$venvPython = Join-Path $installRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    $venvResult = Invoke-LocalProcess -FilePath $pythonPathResolved -Arguments @(
        '-m', 'venv', (Join-Path $installRoot '.venv')
    )
    if ($venvResult.ExitCode -ne 0) {
        throw "Could not create the plugin-local Python environment. $($venvResult.Stderr.Trim())"
    }
}
$pipResult = Invoke-LocalProcess -FilePath $venvPython -Arguments @(
    '-m', 'pip', 'install', '--disable-pip-version-check', '-r',
    (Join-Path $installRoot 'requirements.txt'), '-r',
    (Join-Path $installRoot 'web-panel\host\requirements.txt'), '-r',
    (Join-Path $installRoot 'requirements-bridge.txt')
)
if ($pipResult.ExitCode -ne 0) {
    throw "Installing the pinned MCP and WebView runtime failed. Check the configured Python package source and retry. $($pipResult.Stderr.Trim())"
}

$comspec = $env:ComSpec
if ([string]::IsNullOrWhiteSpace($comspec)) {
    $comspec = Join-Path $env:WINDIR 'System32\cmd.exe'
}
$launcher = Join-Path $installRoot 'scripts\launch_bridge.cmd'
$mcpCommand = $comspec
$mcpWorkingDirectory = $deviceWorkspace
if ($McpHost -eq 'Wsl') {
    # Codex starts the process in Linux; cmd.exe still receives Windows arguments.
    $wsl = Join-Path $env:WINDIR 'System32\wsl.exe'
    $mappedCommand = Invoke-LocalProcess -FilePath $wsl -Arguments @('--exec', 'wslpath', '-a', '-u', $comspec)
    $mappedWorkspace = Invoke-LocalProcess -FilePath $wsl -Arguments @('--exec', 'wslpath', '-a', '-u', $deviceWorkspace)
    if ($mappedCommand.ExitCode -ne 0 -or $mappedWorkspace.ExitCode -ne 0 -or
        -not $mappedCommand.Stdout.Trim().StartsWith('/') -or
        -not $mappedWorkspace.Stdout.Trim().StartsWith('/')) {
        throw 'Could not resolve MCP command and workspace through the default WSL distribution.'
    }
    $mcpCommand = $mappedCommand.Stdout.Trim()
    $mcpWorkingDirectory = $mappedWorkspace.Stdout.Trim()
}
$mcpConfig = [ordered]@{
    mcpServers = [ordered]@{
        'esp32-codex' = [ordered]@{
            command = $mcpCommand
            args = @('/d', '/c', $launcher)
            cwd = $mcpWorkingDirectory
            enabled = $true
            startup_timeout_sec = 30
            tool_timeout_sec = 1800
        }
    }
}
$mcpJson = $mcpConfig | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText((Join-Path $installRoot '.mcp.json'), $mcpJson + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
$pythonw = Join-Path $installRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
    throw "pythonw.exe is required to launch the panel without a console window: $pythonw"
}
New-Item -ItemType Directory -Force -Path $programsPath | Out-Null
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '-B -X utf8 -m panel.launcher --ui web --mode bridge --workspace "' + $deviceWorkspace + '" --bridge-script "' + (Join-Path $installRoot 'bridge.py') + '" --enable-control-tools --enable-write-tools'
$shortcut.WorkingDirectory = Join-Path $installRoot 'mcp-server'
$shortcut.Description = 'ESP32 Codex compact web panel (real bridge; connect only when selected)'
$shortcut.Save()
$mockWebShortcut = $shell.CreateShortcut($mockWebShortcutPath)
$mockWebShortcut.TargetPath = $pythonw
$mockWebShortcut.Arguments = '-B -X utf8 -m panel.launcher --ui web --mode mock --mock-scenario fileops'
$mockWebShortcut.WorkingDirectory = Join-Path $installRoot 'mcp-server'
$mockWebShortcut.Description = 'ESP32 Codex compact web panel (mock fallback)'
$mockWebShortcut.Save()
$tkFallbackShortcut = $shell.CreateShortcut($tkFallbackShortcutPath)
$tkFallbackShortcut.TargetPath = $pythonw
$tkFallbackShortcut.Arguments = '-B -X utf8 -m panel.launcher --ui tk --mode mock --mock-scenario fileops'
$tkFallbackShortcut.WorkingDirectory = Join-Path $installRoot 'mcp-server'
$tkFallbackShortcut.Description = 'ESP32 Codex Tk fallback panel (mock only; no automatic serial connection)'
$tkFallbackShortcut.Save()
Write-Output "Installed ESP32 Codex package source: $installRoot"
Write-Output "Personal marketplace: $marketplacePath"
Write-Output "The MCP configuration launches the real bridge for: $deviceWorkspace"
Write-Output 'The bridge never connects to a serial port automatically.'
Write-Output "Start Menu web entry: $shortcutPath (real bridge; manual connect)"
Write-Output "Mock web fallback: $mockWebShortcutPath"
Write-Output "Tk fallback entry: $tkFallbackShortcutPath (mock)"
Write-Output 'The existing personal marketplace registration was preserved.'
