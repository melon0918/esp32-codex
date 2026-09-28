# UI-6 foreground acceptance: temp Start-Menu-equivalent .lnk and real MCP open tool.
# It does not install into the user's profile or touch physical ESP32 devices.
$ErrorActionPreference='Stop'
$repo=(Get-Location).Path
$py=Join-Path $env:TEMP 'esp32-ui5-isolated-venv\Scripts\python.exe'
$pythonw=Join-Path $env:TEMP 'esp32-ui5-isolated-venv\Scripts\pythonw.exe'
$stage=Join-Path $env:TEMP 'esp32-codex-ui6-entry-stage'
$link=Join-Path $env:TEMP 'esp32-ui6-temp-startmenu-web.lnk'
$titlePattern='ESP32 Codex * MOCK' # ASCII match avoids Windows PowerShell 5.1 UTF-8 source decoding
$report=Join-Path $repo 'web-panel\review\ui6-entries-result.json'
$entry=@{success=$false;source='temporary-link+development-MCP';stage=$stage;link=$link;events=@()}
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class Ui6EntryWindows {
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hwnd);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hwnd,int state);
 [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hwnd,int msg,IntPtr wp,IntPtr lp);
}
'@
function WindowsByTitle {
 return @(Get-Process python,pythonw -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like $titlePattern })
}
function AwaitWindow {
 $limit=(Get-Date).AddSeconds(30)
 while((Get-Date) -lt $limit){
  $items=WindowsByTitle
  if($items.Count -eq 1){return $items[0]}
  Start-Sleep -Milliseconds 250
 }
 throw 'Single visible web panel did not appear'
}
if((WindowsByTitle).Count){throw 'An existing panel belongs to another session; will not touch it'}
$original=$null
try{
 if(-not (Test-Path $py) -or -not(Test-Path $pythonw)){throw 'Isolated runtime missing'}
 # build_plugin_package requires the seed script to exist at a custom target.
 New-Item -ItemType Directory -Path (Join-Path $stage 'scripts') -Force | Out-Null
 Copy-Item (Join-Path $repo 'plugins\esp32-codex\scripts\launch_mock.cmd') (Join-Path $stage 'scripts\launch_mock.cmd') -Force
 & $py -B (Join-Path $repo 'scripts\build_plugin_package.py') --plugin-root $stage
 if($LASTEXITCODE){throw 'Staged package build failed'}
 $shell=New-Object -ComObject WScript.Shell
 $sc=$shell.CreateShortcut($link)
 $sc.TargetPath=$pythonw
 $sc.Arguments='-B -X utf8 -m panel.launcher --ui web --mode mock --mock-scenario fileops'
 $sc.WorkingDirectory=Join-Path $stage 'mcp-server'
 $sc.Description='UI6 temporary mock-only acceptance link'
 $sc.Save()
 $sc2=$shell.CreateShortcut($link)
 if($sc2.TargetPath -ne $pythonw -or $sc2.Arguments -ne $sc.Arguments){throw 'Link contract mismatch'}
 $entry.events+=@{step='temp_shortcut';target=$sc2.TargetPath;arguments=$sc2.Arguments;workingDirectory=$sc2.WorkingDirectory}
 Start-Process -FilePath $link
 $original=AwaitWindow
 $pid0=$original.Id;$handle=[IntPtr]$original.MainWindowHandle
 $entry.events+=@{step='first_visible_entry';pid=$pid0;handle=$handle.ToInt64();count=(WindowsByTitle).Count}
 # A native HWND appears before WebView2 has painted its page. Wait for the
 # browser surface before recording a staged-package screenshot.
 Start-Sleep -Seconds 5
 $snap=Join-Path $repo 'web-panel\review\ui6-visible-entry.png'
 & (Join-Path $repo 'web-panel\host\capture_visible_ui6.ps1') -WindowHandle $handle.ToInt64() -Output $snap
 $snapSize=(Get-Item $snap).Length
 if($snapSize -lt 65000){throw 'Staged window screenshot is blank or not yet painted'}
 $entry.events+=@{step='window_screenshot';size=$snapSize}
 Start-Process -FilePath $link
 Start-Sleep -Seconds 2
 $again=WindowsByTitle
 if($again.Count -ne 1 -or $again[0].Id -ne $pid0){throw 'Repeat shortcut created another visible window'}
 $entry.events+=@{step='second_shortcut_singleton';pid=$again[0].Id;count=$again.Count}
 [void][Ui6EntryWindows]::ShowWindow($handle,6)
 Start-Sleep -Milliseconds 300
 $iconic=[Ui6EntryWindows]::IsIconic($handle)
 if(-not $iconic){throw 'Could not minimize for repeat-open focus test'}
 $sdkFile=Join-Path $env:TEMP 'esp32-ui6-mcp-open.py'
 $mcpSource=@'
import asyncio,ctypes,json,sys
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
async def main():
 root=Path(sys.argv[1])
 params=StdioServerParameters(command=sys.executable,args=["-B","-X","utf8",str(root/"mcp-server"/"server.py"),"--mode","mock","--mock-scenario","fileops"],cwd=str(root))
 async with stdio_client(params) as (r,w):
  async with ClientSession(r,w) as s:
   await s.initialize()
   data=await s.call_tool("esp32_open_panel",{"ui":"web"})
   value=data.structuredContent
   if not isinstance(value,dict):
    value=json.loads(data.content[0].text)
   if not value.get("ok") or not value.get("launchRequested") or value.get("ui")!="web":raise AssertionError(value)
   # A request is asynchronous: keep the MCP parent alive until the pythonw
   # launcher has time to acquire the existing mutex and restore the window.
   u=ctypes.WinDLL("user32",use_last_error=True)
   u.IsIconic.argtypes=[ctypes.c_void_p]
   handle=ctypes.c_void_p(int(sys.argv[2]))
   for _ in range(50):
    if not u.IsIconic(handle):break
    await asyncio.sleep(.3)
   value["restoredObserved"]=not bool(u.IsIconic(handle))
   print(json.dumps(value,ensure_ascii=False),flush=True)
   if not value["restoredObserved"]:raise AssertionError("MCP launch did not restore panel")
asyncio.run(main())
'@
 [IO.File]::WriteAllText($sdkFile,$mcpSource,[Text.UTF8Encoding]::new($false))
 $psi=[Diagnostics.ProcessStartInfo]::new()
 $psi.FileName=$py;$psi.Arguments='-B "'+$sdkFile+'" "'+$stage+'" "'+$handle.ToInt64()+'"' # Windows PowerShell 5.1 lacks ArgumentList
 $psi.WorkingDirectory=$stage;$psi.UseShellExecute=$false;$psi.CreateNoWindow=$true
 $psi.RedirectStandardOutput=$true;$psi.RedirectStandardError=$true
 $process=[Diagnostics.Process]::Start($psi)
 $out=$process.StandardOutput.ReadToEndAsync();$err=$process.StandardError.ReadToEndAsync()
 if(-not $process.WaitForExit(45000)){$process.Kill();throw 'MCP open tool timeout'}
 if($process.ExitCode){throw ('MCP open error: '+$out.Result+' '+$err.Result)}
 $entry.events+=@{step='mcp_tool';payload=($out.Result.Trim()|ConvertFrom-Json)}
 $deadline=(Get-Date).AddSeconds(20)
 $restored=$false
 do {
  Start-Sleep -Milliseconds 350
  $restored=-not [Ui6EntryWindows]::IsIconic($handle)
 } until ($restored -or (Get-Date) -ge $deadline)
 $last=WindowsByTitle
 if($last.Count -ne 1 -or $last[0].Id -ne $pid0){throw 'MCP route created a second window'}
 $foreground=[Ui6EntryWindows]::GetForegroundWindow().ToInt64()
 $entry.events+=@{step='mcp_singleton_focus';count=$last.Count;originalPid=$pid0;restored=$restored;foreground=$foreground;handle=$handle.ToInt64()}
 if(-not $restored){throw 'MCP open did not restore the minimized existing panel'}
 $entry.success=$true
} catch {
 $entry.error=$_.Exception.Message
} finally {
 if($original){
  [void][Ui6EntryWindows]::SendMessage([IntPtr]$original.MainWindowHandle,0x0010,[IntPtr]::Zero,[IntPtr]::Zero)
  Start-Sleep -Seconds 3
  if(Get-Process -Id $original.Id -ErrorAction SilentlyContinue){
   Stop-Process -Id $original.Id -Force -ErrorAction SilentlyContinue
   $entry.events+=@{step='forced_fixture_cleanup';pid=$original.Id}
  } else {$entry.events+=@{step='closed_window';pid=$original.Id}}
 }
 Remove-Item $link -Force -ErrorAction SilentlyContinue
 $entry.events+=@{step='no_real_serial_or_install';installedUntouched=$true}
 [IO.File]::WriteAllText($report,($entry|ConvertTo-Json -Depth 12),[Text.UTF8Encoding]::new($false))
 Write-Output ($entry|ConvertTo-Json -Depth 12 -Compress)
}
if(-not $entry.success){throw ('UI-6 entry check failed: '+$entry.error)}
