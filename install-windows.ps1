$ErrorActionPreference = 'Stop'
$launcher = Join-Path $PSScriptRoot 'run-windows.ps1'
if (-not (Test-Path $launcher)) { throw 'Keep this script in the Webcorder folder.' }
$menu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$shortcut = Join-Path $menu 'Webcorder.lnk'
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($shortcut)
$link.TargetPath = (Get-Command powershell.exe).Source
$link.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$launcher`""
$link.WorkingDirectory = $PSScriptRoot
$link.IconLocation = (Join-Path $PSScriptRoot 'assets\webcorder-icon.ico')
$link.Description = 'Screen and camera recorder'
$link.Save()
Write-Host "Installed Start menu shortcut: $shortcut"
