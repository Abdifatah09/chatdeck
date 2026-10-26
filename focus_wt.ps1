# Brings the Windows Terminal tab titled $env:DASH_MARK to the front, on any virtual desktop.
# Used by terminals.py; set $env:DASH_VDLIB to VirtualDesktopLib.cs to switch desktops reliably.
$m = $env:DASH_MARK; $lib = $env:DASH_VDLIB
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type @'
using System; using System.Text; using System.Runtime.InteropServices; using System.Collections.Generic;
public static class F {
  delegate bool P(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(P cb, IntPtr l);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] static extern void keybd_event(byte k, byte s, uint f, UIntPtr e);
  [DllImport("dwmapi.dll")] static extern int DwmGetWindowAttribute(IntPtr h, int a, out int v, int s);
  public static List<IntPtr> Terminals() { var r = new List<IntPtr>();
    EnumWindows((h, l) => { var c = new StringBuilder(64); GetClassName(h, c, 64);
      if (c.ToString() == "CASCADIA_HOSTING_WINDOW_CLASS") r.Add(h); return true; }, IntPtr.Zero); return r; }
  public static string Title(IntPtr h) { var t = new StringBuilder(512); GetWindowText(h, t, 512); return t.ToString(); }
  public static bool Cloaked(IntPtr h) { int v; DwmGetWindowAttribute(h, 14, out v, 4); return v != 0; }   // on another virtual desktop
  public static bool Raise(IntPtr h) {
    if (IsIconic(h)) ShowWindow(h, 9);
    keybd_event(0x12, 0, 0, UIntPtr.Zero); keybd_event(0x12, 0, 2, UIntPtr.Zero);   // Alt tap lets us take focus
    return SetForegroundWindow(h);
  }
}
'@
$vd = $false
function Show($h) {
  if ([F]::Cloaked($h) -and $lib -and (Test-Path $lib)) {
    if (-not $script:vd) { Add-Type -Path $lib; $script:vd = $true }
    [VirtualDesktop.Desktop]::FromWindow($h).MakeVisible(); Start-Sleep -Milliseconds 250
  }
  [F]::Raise($h) | Out-Null
}
$A = [System.Windows.Automation.AutomationElement]
$tabCond = New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, [System.Windows.Automation.ControlType]::TabItem)
function Tab($h) {   # the tab carrying the mark, if this window's tabs are readable (not on another desktop)
  foreach ($t in $A::FromHandle($h).FindAll([System.Windows.Automation.TreeScope]::Descendants, $tabCond)) { if ($t.Current.Name -eq $m) { return $t } }
}
for ($i = 0; $i -lt 6; $i++) {   # the terminal takes a moment to pick up the new title
  $wins = [F]::Terminals()
  foreach ($h in $wins) { if ([F]::Title($h) -eq $m) { Show $h; "ok"; exit } }
  foreach ($h in $wins) { if (-not [F]::Cloaked($h)) { $t = Tab $h; if ($t) {
    $t.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select(); Show $h; "ok"; exit } } }
  Start-Sleep -Milliseconds 250
}
# Last resort: a background tab in a window on another desktop. Those tabs can only be read once
# the window is on screen, so visit each such window, then go back if the chat isn't there.
$back = [F]::GetForegroundWindow()
foreach ($h in $wins) { if ([F]::Cloaked($h)) { Show $h; Start-Sleep -Milliseconds 300; $t = Tab $h; if ($t) {
  $t.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select(); "ok"; exit } } }
if ($back -ne [IntPtr]::Zero) { Show $back }
"missing"
