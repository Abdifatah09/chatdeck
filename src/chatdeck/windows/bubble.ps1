<#
  chatdeck bubble: a small always-on-top button in the bottom-left corner of the screen.
  Click it to pop the dashboard up above it; click it again, or anywhere else, to tuck it away.
  Drag it to move it. Right-click to hide it or for more. Starting it again while it is hidden
  brings it back. The badge counts running chats and turns amber, with a pulsing ring, when a
  chat is waiting for you. The same badge sits in the notification area (by the clock), where a
  click pops the dashboard up in the bottom-right corner instead.

  Needs the dashboard server running, and Chrome or Edge for the pop-up.
  powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File bubble.ps1 [-Port 47123]
#>
param([int]$Port = 47123, [int]$PopupWidth = 460, [int]$PopupHeight = 760)

# One bubble at a time: starting it again just asks the running one to show itself.
$created = $false
$script:mutex = New-Object System.Threading.Mutex($true, "Local\ChatdeckBubble", [ref]$created)
$script:showSignal = New-Object System.Threading.EventWaitHandle($false, [System.Threading.EventResetMode]::AutoReset, "Local\ChatdeckBubbleShow")
if (-not $created) { $script:showSignal.Set() | Out-Null; exit }

Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase, System.Windows.Forms, System.Drawing
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
  [DllImport("user32.dll")] public static extern bool DestroyIcon(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h, System.Text.StringBuilder s, int n);
  public static string ClassOf(IntPtr h) { var s = new System.Text.StringBuilder(128); GetClassName(h, s, 128); return s.ToString(); }
  [DllImport("user32.dll")] static extern void keybd_event(byte k, byte s, uint f, UIntPtr e);
  public static void Front(IntPtr h) {   // an Alt tap lets a background process take focus
    keybd_event(0x12, 0, 0, UIntPtr.Zero); keybd_event(0x12, 0, 2, UIntPtr.Zero); SetForegroundWindow(h);
  }
}
'@
[System.Net.WebRequest]::DefaultWebProxy = $null   # skip proxy detection, which adds seconds per request

$Base = "http://127.0.0.1:$Port"
$PopupTitle = "chatdeck" + [char]0x200B   # reads "chatdeck"; the zero-width space tells the pop-up apart from the full window
$Size = 64
$StateFile = Join-Path $env:APPDATA "chatdeck\bubble.json"
$script:token = $null
$script:shown = $false
$script:shownAt = [DateTime]::MinValue
$script:hiddenUntil = $null   # set while the bubble itself is hidden
$script:anchor = "bubble"     # where the pop-up opens: above the bubble, or above the tray

# --- talking to the server -------------------------------------------------------------------
function Get-Summary {
  if (-not $script:token) {
    $html = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "$Base/").Content
    if ($html -match 'TOKEN="([0-9a-f]+)"') { $script:token = $Matches[1] }
  }
  try { Invoke-RestMethod -TimeoutSec 2 -Headers @{ "X-Token" = $script:token } "$Base/api/summary" }
  catch { $script:token = $null; throw }   # the server may have restarted with a new token
}

# --- the logo: the same shapes as assets/logo.svg (a deck of chat cards) -----------------------
$LogoXaml = @'
<Canvas xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" Width="64" Height="64">
  <Rectangle Canvas.Left="23" Canvas.Top="5" Width="36" Height="28" RadiusX="7" RadiusY="7" Fill="#A9C4F7"/>
  <Rectangle Canvas.Left="14" Canvas.Top="12" Width="38" Height="29" RadiusX="7" RadiusY="7" Fill="#6E9BEE"/>
  <Path Data="M13 20h34a8 8 0 0 1 8 8v16a8 8 0 0 1-8 8H24l-11 9 2-9h-2a8 8 0 0 1-8-8V28a8 8 0 0 1 8-8z">
    <Path.Fill><LinearGradientBrush StartPoint="0,0" EndPoint="1,1"><GradientStop Offset="0" Color="#4C86F0"/><GradientStop Offset="1" Color="#2A5BD7"/></LinearGradientBrush></Path.Fill>
  </Path>
  <Rectangle Canvas.Left="14" Canvas.Top="30" Width="26" Height="4.5" RadiusX="2.25" RadiusY="2.25" Fill="White"/>
  <Rectangle Canvas.Left="14" Canvas.Top="38.5" Width="17" Height="4.5" RadiusX="2.25" RadiusY="2.25" Fill="White" Opacity="0.8"/>
</Canvas>
'@

function Logo-Bitmap($px) {
  # The logo rendered to a System.Drawing bitmap, for the tray icon.
  $vb = New-Object System.Windows.Controls.Viewbox
  $vb.Child = [Windows.Markup.XamlReader]::Parse($LogoXaml)
  $vb.Measure((New-Object System.Windows.Size($px, $px))); $vb.Arrange((New-Object System.Windows.Rect(0, 0, $px, $px)))
  $rtb = New-Object System.Windows.Media.Imaging.RenderTargetBitmap($px, $px, 96, 96, [System.Windows.Media.PixelFormats]::Pbgra32)
  $rtb.Render($vb)
  $enc = New-Object System.Windows.Media.Imaging.PngBitmapEncoder
  $enc.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($rtb))
  $ms = New-Object System.IO.MemoryStream; $enc.Save($ms); $ms.Position = 0
  New-Object System.Drawing.Bitmap($ms)
}

# --- the bubble ------------------------------------------------------------------------------
[xml]$xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="chatdeck bubble" Width="$Size" Height="$Size" WindowStyle="None" AllowsTransparency="True"
        Background="Transparent" Topmost="True" ShowInTaskbar="False" ResizeMode="NoResize" Cursor="Hand">
  <Grid>
    <Ellipse x:Name="Ring" Width="58" Height="58" Stroke="#F5B942" StrokeThickness="3" Opacity="0"/>
    <Ellipse x:Name="Disc" Width="48" Height="48" Fill="#1B1F27" Stroke="#3A4150" StrokeThickness="1">
      <Ellipse.Effect><DropShadowEffect BlurRadius="10" ShadowDepth="2" Opacity="0.45"/></Ellipse.Effect>
    </Ellipse>
    <Viewbox Width="30" Height="30" Margin="0,1,0,0">$LogoXaml</Viewbox>
    <Border x:Name="Badge" Background="#3ECF8E" CornerRadius="9" MinWidth="18" Height="18" HorizontalAlignment="Right"
            VerticalAlignment="Top" Margin="0,5,5,0" Padding="4,0" Visibility="Collapsed">
      <TextBlock x:Name="Count" Foreground="#0F1115" FontSize="11" FontWeight="Bold" HorizontalAlignment="Center" VerticalAlignment="Center"/>
    </Border>
  </Grid>
</Window>
"@
$win = [Windows.Markup.XamlReader]::Load((New-Object System.Xml.XmlNodeReader $xaml))
$ring = $win.FindName("Ring"); $disc = $win.FindName("Disc")
$badge = $win.FindName("Badge"); $count = $win.FindName("Count")
$win.ToolTip = "chatdeck"

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
  # Above the bubble, lined up with whichever edge of the screen the bubble is nearer, or in the
  # bottom-right corner when opened from the tray; kept on screen.
  $wa = [System.Windows.SystemParameters]::WorkArea
  $scale = [System.Windows.PresentationSource]::FromVisual($win).CompositionTarget.TransformToDevice.M11
  if ($script:anchor -eq "tray") { $left = $wa.Right - 8 - $PopupWidth; $top = $wa.Bottom - 8 - $PopupHeight }
  else {
    $left = if ($win.Left + $Size / 2 -lt $wa.Left + $wa.Width / 2) { $win.Left + 4 } else { $win.Left + $Size - 4 - $PopupWidth }
    $top = $win.Top - 6 - $PopupHeight
  }
  if ($top -lt $wa.Top) { $top = $wa.Top }
  $left = [Math]::Max($wa.Left, [Math]::Min($left, $wa.Right - $PopupWidth))
  [U]::SetWindowPos($h, [IntPtr]::Zero, [int]($left * $scale), [int]($top * $scale),
                    [int]($PopupWidth * $scale), [int]($PopupHeight * $scale), 0x14) | Out-Null   # no z-order change, no activate
}

function Show-Popup($from = "bubble") {
  $script:anchor = $from
  $h = Popup-Handle
  if ($h -eq [IntPtr]::Zero) {
    $b = Browser
    if (-not $b) { [System.Windows.MessageBox]::Show("The pop-up needs Chrome or Edge.", "chatdeck") | Out-Null; return }
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

function Toggle-Popup($from = "bubble") { if ($script:shown) { Hide-Popup } else { Show-Popup $from } }

function Hide-Bubble($until) { Hide-Popup; $win.Hide(); $script:hiddenUntil = $until }
function Show-Bubble { $script:hiddenUntil = $null; $win.Show(); [U]::ShowWindow($script:self, 4) | Out-Null }

# Click toggles; dragging moves the bubble (and the pop-up with it).
$win.Add_MouseLeftButtonDown({
  $l = $win.Left; $t = $win.Top
  $win.DragMove()
  if ([Math]::Abs($win.Left - $l) -lt 3 -and [Math]::Abs($win.Top - $t) -lt 3) { Toggle-Popup }
  else { Save-Position; if ($script:shown -and $script:anchor -eq "bubble") { $h = Popup-Handle; if ($h -ne [IntPtr]::Zero) { Place-Popup $h } } }
})

$menu = New-Object System.Windows.Controls.ContextMenu
function Add-Item($text, $action) { $i = New-Object System.Windows.Controls.MenuItem; $i.Header = $text; $i.Add_Click($action); $menu.Items.Add($i) | Out-Null }
Add-Item "Show chats" { Show-Popup }
Add-Item "Open the full dashboard" { $b = Browser; if ($b) { Start-Process $b -ArgumentList "--app=$Base/" } }
Add-Item "Move back to the corner" { Home-Position; Save-Position }
$menu.Items.Add((New-Object System.Windows.Controls.Separator)) | Out-Null
Add-Item "Hide for an hour" { Hide-Bubble ((Get-Date).AddHours(1)) }
Add-Item "Hide (start it again to bring it back)" { Hide-Bubble ([DateTime]::MaxValue) }
Add-Item "Quit" { $win.Close() }
$win.ContextMenu = $menu

# --- the tray icon ------------------------------------------------------------------------------
$tray = New-Object System.Windows.Forms.NotifyIcon
$tray.Text = "chatdeck"
$tray.Add_MouseClick({ param($s, $e) if ($e.Button -eq [System.Windows.Forms.MouseButtons]::Left) { Toggle-Popup "tray" } })
$trayMenu = New-Object System.Windows.Forms.ContextMenuStrip
function Add-TrayItem($text, $action) { $i = $trayMenu.Items.Add($text); $i.Add_Click($action); $i }
Add-TrayItem "Show chats" { Show-Popup "tray" } | Out-Null
Add-TrayItem "Open the full dashboard" { $b = Browser; if ($b) { Start-Process $b -ArgumentList "--app=$Base/" } } | Out-Null
$bubbleItem = Add-TrayItem "Show the corner button" { if ($script:hiddenUntil) { Show-Bubble } else { Hide-Bubble ([DateTime]::MaxValue) } }
$trayMenu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null
Add-TrayItem "Quit" { $win.Close() } | Out-Null
$trayMenu.Add_Opening({ $bubbleItem.Text = $(if ($script:hiddenUntil) { "Show the corner button" } else { "Hide the corner button" }) })
$tray.ContextMenuStrip = $trayMenu

# The tray icon: the logo, with a count badge in its corner.
$script:trayKey = $null; $script:trayIcon = $null; $script:trayHandle = [IntPtr]::Zero
function Set-Tray($label, $argb, $tip) {
  $key = "$label|$argb"
  if ($key -ne $script:trayKey) {
    $bmp = Logo-Bitmap 32
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = "AntiAlias"; $g.TextRenderingHint = "AntiAliasGridFit"
    $center = New-Object System.Drawing.StringFormat; $center.Alignment = "Center"; $center.LineAlignment = "Center"
    if ($label) {
      $g.FillEllipse((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 15, 17, 21))), 11, 11, 23, 23)   # dark ring keeps it apart from the logo
      $g.FillEllipse((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb($argb))), 13, 13, 19, 19)
      $badgeFont = New-Object System.Drawing.Font("Segoe UI", 13, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
      $g.DrawString($label, $badgeFont, (New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 15, 17, 21))),
                    (New-Object System.Drawing.RectangleF(13, 13, 19, 19)), $center)
    }
    $g.Dispose()
    $h = $bmp.GetHicon(); $tray.Icon = [System.Drawing.Icon]::FromHandle($h); $bmp.Dispose()
    if ($script:trayHandle -ne [IntPtr]::Zero) { [U]::DestroyIcon($script:trayHandle) | Out-Null }
    $script:trayHandle = $h; $script:trayKey = $key
  }
  $tray.Text = $(if ($tip.Length -gt 63) { $tip.Substring(0, 60) + "..." } else { $tip })   # Windows caps tray tips at 63 characters
  $tray.Visible = $true
}

$win.Add_Closed({
  $tray.Visible = $false; $tray.Dispose()
  $h = Popup-Handle
  if ($h -ne [IntPtr]::Zero) { [U]::PostMessage($h, 0x10, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null }   # close the pop-up too
})

# --- timers: tuck the pop-up away when focus moves elsewhere, and keep the badge current -------
$script:self = [IntPtr]::Zero
$focusTimer = New-Object System.Windows.Threading.DispatcherTimer
$focusTimer.Interval = [TimeSpan]::FromMilliseconds(300)
$focusTimer.Add_Tick({
  if ($script:showSignal.WaitOne(0) -or ($script:hiddenUntil -and (Get-Date) -ge $script:hiddenUntil)) { Show-Bubble }
  if (-not $script:shown) { return }
  $h = Popup-Handle
  if ($h -eq [IntPtr]::Zero -or -not [U]::IsWindowVisible($h) -or [U]::IsIconic($h)) { $script:shown = $false; return }
  if (((Get-Date) - $script:shownAt).TotalMilliseconds -lt 800) { return }
  $fg = [U]::GetAncestor([U]::GetForegroundWindow(), 3)   # root owner, so the pop-up's own dialogs count as the pop-up
  # The taskbar and tray take focus while you click the tray icon, which toggles the pop-up itself.
  $taskbar = @("Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow", "TopLevelWindowForOverflowXamlIsland") -contains [U]::ClassOf($fg)
  if ($fg -ne $h -and $fg -ne $script:self -and -not $taskbar) { Hide-Popup }
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
      Set-Tray "$($waiting.Count)" 0xFFF5B942 $win.ToolTip
    } else {
      if ($script:wasWaiting) { $ring.BeginAnimation([System.Windows.UIElement]::OpacityProperty, $null); $ring.Opacity = 0 }
      $badge.Background = "#3ECF8E"; $count.Text = $s.running
      $badge.Visibility = $(if ($s.running) { "Visible" } else { "Collapsed" })
      $win.ToolTip = $(if ($s.running) { "$($s.running) running, $($s.busy) working" } else { "No chats running" })
      Set-Tray $(if ($s.running) { "$($s.running)" } else { "" }) 0xFF3ECF8E "chatdeck: $($win.ToolTip)"
    }
    $script:wasWaiting = [bool]$waiting.Count
  } catch {
    $disc.Opacity = 0.45; $badge.Visibility = "Collapsed"; $win.ToolTip = "Dashboard server isn't running"
    Set-Tray "" 0 "chatdeck: the dashboard server isn't running"
  }
}
$pollTimer = New-Object System.Windows.Threading.DispatcherTimer
$pollTimer.Interval = [TimeSpan]::FromSeconds(3)
$pollTimer.Add_Tick({ Update-Badge })

$win.Add_SourceInitialized({ $script:self = (New-Object System.Windows.Interop.WindowInteropHelper($win)).Handle })
# Started with -WindowStyle Hidden, Windows applies "hidden" to the first window the process shows, so show it explicitly.
$win.Add_Loaded({ [U]::ShowWindow($script:self, 4) | Out-Null; Update-Badge; $pollTimer.Start(); $focusTimer.Start() })
$win.ShowDialog() | Out-Null
