param([Parameter(Mandatory=$true)][long]$WindowHandle)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class Ui7CopyCheck {
  [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left; public int Top; public int Right; public int Bottom; }
  [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);
  [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, IntPtr extra);
  [DllImport("user32.dll")] public static extern void keybd_event(byte key, byte scan, uint flags, IntPtr extra);
}
'@

[void][Ui7CopyCheck]::SetThreadDpiAwarenessContext([IntPtr](-4))
$handle = [IntPtr]$WindowHandle
$rect = New-Object Ui7CopyCheck+Rect
if (-not [Ui7CopyCheck]::GetWindowRect($handle, [ref]$rect)) { throw 'GetWindowRect failed' }
$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top
if ($width -lt 800 -or $height -lt 800) { throw "Unexpected preview size: ${width}x${height}" }
$scale = $width / 450.0
[void][Ui7CopyCheck]::SetForegroundWindow($handle)
Start-Sleep -Milliseconds 250

# The copy control is in the console header, at 365x255 CSS pixels in this draft.
[Windows.Forms.Clipboard]::Clear()
[void][Ui7CopyCheck]::SetCursorPos($rect.Left + [int](365 * $scale), $rect.Top + [int](255 * $scale))
[Ui7CopyCheck]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)
[Ui7CopyCheck]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)
Start-Sleep -Milliseconds 350
$buttonCopy = [Windows.Forms.Clipboard]::GetText()
if (-not $buttonCopy.Contains('MOCK')) { throw 'Copy output button did not place console text on the clipboard' }

# Drag-select the visible sample rows, then verify the native Ctrl+C path too.
[Windows.Forms.Clipboard]::Clear()
[void][Ui7CopyCheck]::SetCursorPos($rect.Left + [int](28 * $scale), $rect.Top + [int](286 * $scale))
Start-Sleep -Milliseconds 120
[Ui7CopyCheck]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)
foreach ($step in 1..8) {
  $x = $rect.Left + [int]((28 + (298 * $step / 8)) * $scale)
  $y = $rect.Top + [int]((286 + (39 * $step / 8)) * $scale)
  [void][Ui7CopyCheck]::SetCursorPos($x, $y)
  Start-Sleep -Milliseconds 20
}
[Ui7CopyCheck]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)
[Ui7CopyCheck]::keybd_event(0x11, 0, 0, [IntPtr]::Zero)
[Ui7CopyCheck]::keybd_event(0x43, 0, 0, [IntPtr]::Zero)
[Ui7CopyCheck]::keybd_event(0x43, 0, 0x0002, [IntPtr]::Zero)
[Ui7CopyCheck]::keybd_event(0x11, 0, 0x0002, [IntPtr]::Zero)
Start-Sleep -Milliseconds 350
$selectionCopy = [Windows.Forms.Clipboard]::GetText()
if (-not $selectionCopy.Contains('MOCK')) { throw 'Mouse selection plus Ctrl+C did not copy console text' }

Write-Output ("UI7_COPY_OK hwnd=$WindowHandle dpi=" + [Ui7CopyCheck]::GetDpiForWindow($handle) + " buttonChars=" + $buttonCopy.Length + " selectionChars=" + $selectionCopy.Length)
