<#
  Docks the full chatdeck window to the right edge of the screen it's on, full height.
  Chrome remembers where an app window was last left and ignores --window-size, so the window
  is moved after it opens. Waits up to 10 seconds for it to appear.
  powershell -NoProfile -ExecutionPolicy Bypass -File dock.ps1 [-Width 520]
#>
param([int]$Width = 520)

Add-Type -Namespace Chatdeck -Name Dock -MemberDefinition @'
[StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
[StructLayout(LayoutKind.Sequential)] public struct MONITORINFO { public int cbSize; public RECT rcMonitor, rcWork; public int dwFlags; }
[DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr c);
[DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr FindWindow(string c, string t);
[DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
[DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr h);
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
[DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
[DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int w, int hh, uint f);
[DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr h);
[DllImport("user32.dll")] public static extern IntPtr MonitorFromWindow(IntPtr h, uint f);
[DllImport("user32.dll")] public static extern bool GetMonitorInfo(IntPtr m, ref MONITORINFO i);
[DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr h, int a, out RECT r, int n);
'@
[Chatdeck.Dock]::SetThreadDpiAwarenessContext([IntPtr](-4)) | Out-Null   # real pixels on every monitor, whatever its scaling

$h = [IntPtr]::Zero
for ($i = 0; $i -lt 100 -and $h -eq [IntPtr]::Zero; $i++) {
  $h = [Chatdeck.Dock]::FindWindow("Chrome_WidgetWin_1", "chatdeck")
  if ($h -eq [IntPtr]::Zero) { Start-Sleep -Milliseconds 100 }
}
if ($h -eq [IntPtr]::Zero) { exit }
if ([Chatdeck.Dock]::IsIconic($h) -or [Chatdeck.Dock]::IsZoomed($h)) { [Chatdeck.Dock]::ShowWindow($h, 9) | Out-Null }

$mi = New-Object Chatdeck.Dock+MONITORINFO; $mi.cbSize = [Runtime.InteropServices.Marshal]::SizeOf($mi)
[Chatdeck.Dock]::GetMonitorInfo([Chatdeck.Dock]::MonitorFromWindow($h, 2), [ref]$mi) | Out-Null
$wa = $mi.rcWork
$w = [int]($Width * [Chatdeck.Dock]::GetDpiForWindow($h) / 96)

# Windows draws an invisible resize border around the window; size it so the visible edges sit flush.
$win = New-Object Chatdeck.Dock+RECT; $vis = New-Object Chatdeck.Dock+RECT
[Chatdeck.Dock]::GetWindowRect($h, [ref]$win) | Out-Null
$l = 0; $r = 0; $b = 0
if ([Chatdeck.Dock]::DwmGetWindowAttribute($h, 9, [ref]$vis, 16) -eq 0) {   # DWMWA_EXTENDED_FRAME_BOUNDS
  $l = $vis.Left - $win.Left; $r = $win.Right - $vis.Right; $b = $win.Bottom - $vis.Bottom
}
[Chatdeck.Dock]::SetWindowPos($h, [IntPtr]::Zero, $wa.Right - $w - $l, $wa.Top, $w + $l + $r, $wa.Bottom - $wa.Top + $b, 0x14) | Out-Null   # no z-order change, no activate
