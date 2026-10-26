<#
  Claude Chats bubble: a small always-on-top button in the bottom-left corner of the screen.
  Click it to pop the dashboard up above it; click it again, or anywhere else, to tuck it away.
  Drag it to move it. Right-click for more. The badge counts running chats and turns amber,
  with a pulsing ring, when a chat is waiting for you.

  Needs the dashboard server running, and Chrome or Edge for the pop-up.
  powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File bubble.ps1 [-Port 47123]
#>
param([int]$Port = 47123, [int]$PopupWidth = 460, [int]$PopupHeight = 760)

Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase
Add-Type @'
using System; using System.Runtime.InteropServices;
public static class U {
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr FindWindow(string c, string t);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr h, uint f);
  [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int w, int hh, uint f);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] static extern void keybd_event(byte k, byte s, uint f, UIntPtr e);
  public static void Front(IntPtr h) {   // an Alt tap lets a background process take focus
    keybd_event(0x12, 0, 0, UIntPtr.Zero); keybd_event(0x12, 0, 2, UIntPtr.Zero); SetForegroundWindow(h);
  }
}
'@
[System.Net.WebRequest]::DefaultWebProxy = $null   # skip proxy detection, which adds seconds per request

$Base = "http://127.0.0.1:$Port"
$PopupTitle = "Claude Chats Popup"
$Size = 64
$StateFile = Join-Path $env:APPDATA "claude-dash\bubble.json"
$script:token = $null
$script:shown = $false
$script:shownAt = [DateTime]::MinValue

# --- talking to the server -------------------------------------------------------------------
function Get-Summary {
  if (-not $script:token) {
    $html = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "$Base/").Content
    if ($html -match 'TOKEN="([0-9a-f]+)"') { $script:token = $Matches[1] }
  }
  try { Invoke-RestMethod -TimeoutSec 2 -Headers @{ "X-Token" = $script:token } "$Base/api/summary" }
  catch { $script:token = $null; throw }   # the server may have restarted with a new token
}

# --- the bubble ------------------------------------------------------------------------------
[xml]$xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Claude Chats Bubble" Width="$Size" Height="$Size" WindowStyle="None" AllowsTransparency="True"
        Background="Transparent" Topmost="True" ShowInTaskbar="False" ResizeMode="NoResize" Cursor="Hand">
  <Grid>
    <Ellipse x:Name="Ring" Width="58" Height="58" Stroke="#F5B942" StrokeThickness="3" Opacity="0"/>
    <Ellipse x:Name="Disc" Width="48" Height="48" Fill="#1B1F27" Stroke="#3A4150" StrokeThickness="1">
      <Ellipse.Effect><DropShadowEffect BlurRadius="10" ShadowDepth="2" Opacity="0.45"/></Ellipse.Effect>
    </Ellipse>
    <TextBlock x:Name="Glyph" FontSize="24" Foreground="#E6E8EE" HorizontalAlignment="Center" VerticalAlignment="Center"
               FontFamily="Segoe UI Symbol" Margin="0,0,0,2"/>
    <Border x:Name="Badge" Background="#3ECF8E" CornerRadius="9" MinWidth="18" Height="18" HorizontalAlignment="Right"
            VerticalAlignment="Top" Margin="0,5,5,0" Padding="4,0" Visibility="Collapsed">
      <TextBlock x:Name="Count" Foreground="#0F1115" FontSize="11" FontWeight="Bold" HorizontalAlignment="Center" VerticalAlignment="Center"/>
    </Border>
  </Grid>
</Window>
"@
$win = [Windows.Markup.XamlReader]::Load((New-Object System.Xml.XmlNodeReader $xaml))
$ring = $win.FindName("Ring"); $disc = $win.FindName("Disc"); $glyph = $win.FindName("Glyph")
$badge = $win.FindName("Badge"); $count = $win.FindName("Count")
$glyph.Text = [string][char]0x2733
$win.ToolTip = "Claude chats"

$pulse = New-Object System.Windows.Media.Animation.DoubleAnimation(0.25, 1, [TimeSpan]::FromMilliseconds(700))
$pulse.AutoReverse = $true; $pulse.RepeatBehavior = [System.Windows.Media.Animation.RepeatBehavior]::Forever

function Home-Position {
  $wa = [System.Windows.SystemParameters]::WorkArea
  $win.Left = $wa.Left + 12; $win.Top = $wa.Bottom - $Size - 12
}
function Save-Position {
  try {
    New-Item -ItemType Directory -Force (Split-Path $StateFile) | Out-Null
    @{ left = $win.Left; top = $win.Top } | ConvertTo-Json | Set-Content $StateFile
  } catch {}
}
Home-Position
try {
  $p = Get-Content $StateFile -Raw | ConvertFrom-Json
  $vs = [System.Windows.SystemParameters]
  if ($p.left -ge $vs::VirtualScreenLeft -and $p.left -le $vs::VirtualScreenLeft + $vs::VirtualScreenWidth - $Size -and
      $p.top -ge $vs::VirtualScreenTop -and $p.top -le $vs::VirtualScreenTop + $vs::VirtualScreenHeight - $Size) {
    $win.Left = $p.left; $win.Top = $p.top
  }
} catch {}

# --- the pop-up: a Chrome/Edge app window placed above the bubble ----------------------------
function Popup-Handle { [U]::FindWindow("Chrome_WidgetWin_1", $PopupTitle) }

function Browser {
  foreach ($b in @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
                   "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
                   "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe")) { if (Test-Path $b) { return $b } }
}

function Place-Popup($h) {
  # Above the bubble, lined up with whichever edge of the screen the bubble is nearer; kept on screen.
  $wa = [System.Windows.SystemParameters]::WorkArea
  $scale = [System.Windows.PresentationSource]::FromVisual($win).CompositionTarget.TransformToDevice.M11
  $left = if ($win.Left + $Size / 2 -lt $wa.Left + $wa.Width / 2) { $win.Left + 4 } else { $win.Left + $Size - 4 - $PopupWidth }
  $top = $win.Top - 6 - $PopupHeight
  if ($top -lt $wa.Top) { $top = $wa.Top }
  $left = [Math]::Max($wa.Left, [Math]::Min($left, $wa.Right - $PopupWidth))
  [U]::SetWindowPos($h, [IntPtr]::Zero, [int]($left * $scale), [int]($top * $scale),
                    [int]($PopupWidth * $scale), [int]($PopupHeight * $scale), 0x14) | Out-Null   # no z-order change, no activate
}

function Show-Popup {
  $h = Popup-Handle
  if ($h -eq [IntPtr]::Zero) {
    $b = Browser
    if (-not $b) { [System.Windows.MessageBox]::Show("The pop-up needs Chrome or Edge.", "Claude chats") | Out-Null; return }
    Start-Process $b -ArgumentList "--app=$Base/?popup=1", "--window-size=$PopupWidth,$PopupHeight"
    for ($i = 0; $i -lt 60 -and $h -eq [IntPtr]::Zero; $i++) { Start-Sleep -Milliseconds 100; $h = Popup-Handle }
    if ($h -eq [IntPtr]::Zero) { return }
  }
  Place-Popup $h
  [U]::ShowWindow($h, $(if ([U]::IsIconic($h)) { 9 } else { 5 })) | Out-Null
  [U]::Front($h)
  $script:shown = $true; $script:shownAt = Get-Date
}

function Hide-Popup {
  $h = Popup-Handle
  if ($h -ne [IntPtr]::Zero) { [U]::ShowWindow($h, 0) | Out-Null }
  $script:shown = $false
}

function Toggle-Popup { if ($script:shown) { Hide-Popup } else { Show-Popup } }

# Click toggles; dragging moves the bubble (and the pop-up with it).
$win.Add_MouseLeftButtonDown({
  $l = $win.Left; $t = $win.Top
  $win.DragMove()
  if ([Math]::Abs($win.Left - $l) -lt 3 -and [Math]::Abs($win.Top - $t) -lt 3) { Toggle-Popup }
  else { Save-Position; if ($script:shown) { $h = Popup-Handle; if ($h -ne [IntPtr]::Zero) { Place-Popup $h } } }
})

$menu = New-Object System.Windows.Controls.ContextMenu
function Add-Item($text, $action) { $i = New-Object System.Windows.Controls.MenuItem; $i.Header = $text; $i.Add_Click($action); $menu.Items.Add($i) | Out-Null }
Add-Item "Show chats" { Show-Popup }
Add-Item "Open the full dashboard" { $b = Browser; if ($b) { Start-Process $b -ArgumentList "--app=$Base/" } }
Add-Item "Move back to the corner" { Home-Position; Save-Position }
$menu.Items.Add((New-Object System.Windows.Controls.Separator)) | Out-Null
Add-Item "Quit" { $win.Close() }
$win.ContextMenu = $menu

$win.Add_Closed({
  $h = Popup-Handle
  if ($h -ne [IntPtr]::Zero) { [U]::PostMessage($h, 0x10, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null }   # close the pop-up too
})

# --- timers: tuck the pop-up away when focus moves elsewhere, and keep the badge current -------
$script:self = [IntPtr]::Zero
$focusTimer = New-Object System.Windows.Threading.DispatcherTimer
$focusTimer.Interval = [TimeSpan]::FromMilliseconds(300)
$focusTimer.Add_Tick({
  if (-not $script:shown) { return }
  $h = Popup-Handle
  if ($h -eq [IntPtr]::Zero -or -not [U]::IsWindowVisible($h) -or [U]::IsIconic($h)) { $script:shown = $false; return }
  if (((Get-Date) - $script:shownAt).TotalMilliseconds -lt 800) { return }
  $fg = [U]::GetAncestor([U]::GetForegroundWindow(), 3)   # root owner, so the pop-up's own dialogs count as the pop-up
  if ($fg -ne $h -and $fg -ne $script:self) { Hide-Popup }
})

$script:wasWaiting = $false
function Update-Badge {
  try {
    $s = Get-Summary
    $disc.Opacity = 1
    $waiting = @($s.waiting)
    if ($waiting.Count) {
      $badge.Background = "#F5B942"; $count.Text = $waiting.Count; $badge.Visibility = "Visible"
      $win.ToolTip = "Waiting for you: " + ($waiting -join ", ")
      if (-not $script:wasWaiting) { $ring.BeginAnimation([System.Windows.UIElement]::OpacityProperty, $pulse) }
    } else {
      if ($script:wasWaiting) { $ring.BeginAnimation([System.Windows.UIElement]::OpacityProperty, $null); $ring.Opacity = 0 }
      $badge.Background = "#3ECF8E"; $count.Text = $s.running
      $badge.Visibility = $(if ($s.running) { "Visible" } else { "Collapsed" })
      $win.ToolTip = $(if ($s.running) { "$($s.running) running, $($s.busy) working" } else { "No chats running" })
    }
    $script:wasWaiting = [bool]$waiting.Count
  } catch {
    $disc.Opacity = 0.45; $badge.Visibility = "Collapsed"; $win.ToolTip = "Dashboard server isn't running"
  }
}
$pollTimer = New-Object System.Windows.Threading.DispatcherTimer
$pollTimer.Interval = [TimeSpan]::FromSeconds(3)
$pollTimer.Add_Tick({ Update-Badge })

$win.Add_SourceInitialized({ $script:self = (New-Object System.Windows.Interop.WindowInteropHelper($win)).Handle })
# Started with -WindowStyle Hidden, Windows applies "hidden" to the first window the process shows, so show it explicitly.
$win.Add_Loaded({ [U]::ShowWindow($script:self, 4) | Out-Null; Update-Badge; $pollTimer.Start(); $focusTimer.Start() })
$win.ShowDialog() | Out-Null
