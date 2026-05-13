$sysmonDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ps1       = Join-Path $sysmonDir "run.ps1"
$desktop   = [Environment]::GetFolderPath("Desktop")
$shortcut  = Join-Path $desktop "System Monitor.lnk"

$wsh = New-Object -ComObject WScript.Shell
$lnk = $wsh.CreateShortcut($shortcut)
$lnk.TargetPath       = "powershell.exe"
$lnk.Arguments        = "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ps1`""
$lnk.WorkingDirectory = $sysmonDir
$lnk.Description      = "Claude System Monitor"
$lnk.IconLocation     = "C:\Windows\System32\perfmon.exe,0"
$lnk.WindowStyle      = 1
$lnk.Save()

Write-Host ""
Write-Host "  Shortcut updated:" -ForegroundColor Green
Write-Host "  $shortcut" -ForegroundColor Cyan
Write-Host ""
