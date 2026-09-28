param([Parameter(Mandatory=$true)][long]$WindowHandle,[Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class Ui6Window {
 [StructLayout(LayoutKind.Sequential)] public struct Rect {public int Left;public int Top;public int Right;public int Bottom;}
 [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr FindWindow(string cls,string title);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr handle,out Rect rect);
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr handle);
 [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr handle);
 [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
 [DllImport("user32.dll", SetLastError=true)] public static extern bool PrintWindow(IntPtr handle,IntPtr hdc,uint flags);
}
'@
# The host has 200% per-monitor DPI: screenshot process must use physical
# device pixels, not the DPI-unaware virtualized GetWindowRect dimensions.
$priorDpiContext=[Ui6Window]::SetThreadDpiAwarenessContext([IntPtr](-4))
$handle=[IntPtr]$WindowHandle
if($handle -eq [IntPtr]::Zero){throw 'UI6 window handle must be nonzero'}
$r=New-Object Ui6Window+Rect
if(-not [Ui6Window]::GetWindowRect($handle,[ref]$r)){throw 'GetWindowRect failed'}
$width=$r.Right-$r.Left;$height=$r.Bottom-$r.Top
if($width -lt 400 -or $height -lt 400){throw ("Window size unexpected: "+$width+"x"+$height)}
[void][Ui6Window]::SetForegroundWindow($handle)
Start-Sleep -Milliseconds 240
$bmp=New-Object System.Drawing.Bitmap($width,$height)
$graphics=[System.Drawing.Graphics]::FromImage($bmp)
try {
 # Capture the window's own DC; CopyFromScreen in a DPI-unaware PowerShell
 # process captured unrelated desktop pixels at 200% scale.
 $hdc=$graphics.GetHdc()
 try{$printed=[Ui6Window]::PrintWindow($handle,$hdc,2)}finally{$graphics.ReleaseHdc($hdc)}
 if(-not $printed){throw 'PrintWindow failed; refusing to save unrelated desktop pixels'}
 $parent=Split-Path -Parent $Output
 if(-not(Test-Path -LiteralPath $parent)){[void](New-Item -ItemType Directory -Path $parent -Force)}
 $bmp.Save($Output,[System.Drawing.Imaging.ImageFormat]::Png)
} finally {$graphics.Dispose();$bmp.Dispose()}
$size=(Get-Item -LiteralPath $Output).Length
if($size -lt 8000){throw ("Unexpected PNG file size "+$size)}
Write-Output ("CAPTURE width="+$width+" height="+$height+" dpi="+[Ui6Window]::GetDpiForWindow($handle)+" bytes="+$size+" file="+$Output)
