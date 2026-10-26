# chatdeck launcher for Windows (written by `chatdeck autostart on`): starts the chatdeck server
# in WSL if it isn't running, then the corner bubble and tray icon. With -Open it also opens the
# full dashboard window, or brings it forward if it's already open.
param([switch]$Open)
$Port = __PORT__
$Distro = "__DISTRO__"
$Cmd = "__CMD__"
$Url = "http://127.0.0.1:$Port/"
[System.Net.WebRequest]::DefaultWebProxy = $null
function Up { try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 $Url).Content -match "<title>chatdeck</title>" } catch { $false } }

if (-not (Up)) {
  # A login shell, so the server finds `claude` on the same PATH as in a terminal.
  Start-Process wsl.exe -ArgumentList "-d $Distro -- bash -lc ""$Cmd serve --port $Port""" -WindowStyle Hidden
  for ($i = 0; $i -lt 60 -and -not (Up); $i++) { Start-Sleep -Milliseconds 500 }
}

Start-Process powershell.exe -WindowStyle Hidden -ArgumentList "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
  "-File", "$PSScriptRoot\bubble.ps1", "-Port", "$Port"

if ($Open) {
  Add-Type -Namespace Chatdeck -Name W -MemberDefinition @'
[DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr FindWindow(string c, string t);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
'@
  $h = [Chatdeck.W]::FindWindow("Chrome_WidgetWin_1", "chatdeck")
  if ($h -ne [IntPtr]::Zero) { [Chatdeck.W]::ShowWindow($h, 9) | Out-Null; [Chatdeck.W]::SetForegroundWindow($h) | Out-Null }
  else {
    $b = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
           "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe") |
         Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($b) { Start-Process $b -ArgumentList "--app=$Url", "--window-size=520,900" } else { Start-Process $Url }
  }
}
