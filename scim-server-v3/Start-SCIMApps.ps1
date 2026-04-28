<#
.SYNOPSIS
    Launches all three SSO + SCIM Service Provider apps simultaneously.
.PARAMETER NoBrowser
    Skip opening dashboards in the browser.
.PARAMETER CreateShortcut
    Creates a desktop shortcut to launch all apps with one click.
#>

param(
    [switch]$NoBrowser,
    [switch]$CreateShortcut
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

if ($CreateShortcut) {
    $Desktop = [Environment]::GetFolderPath("Desktop")
    $ShortcutPath = Join-Path $Desktop "SCIM Lab Apps.lnk"
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = "powershell.exe"
    $Shortcut.Arguments = "-ExecutionPolicy Bypass -File `"$ScriptDir\Start-SCIMApps.ps1`""
    $Shortcut.WorkingDirectory = $ScriptDir
    $Shortcut.Description = "Start all SSO + SCIM Service Provider apps"
    $Shortcut.IconLocation = "powershell.exe,0"
    $Shortcut.Save()
    Write-Host ""
    Write-Host "  Desktop shortcut created: $ShortcutPath" -ForegroundColor Green
    Write-Host "  Double-click 'SCIM Lab Apps' on your desktop to launch all apps." -ForegroundColor Gray
    Write-Host ""
    return
}

$PythonCmd = $null
$PythonPaths = @(
    (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
    (Get-Command python3 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
    "C:\Users\Administrator\AppData\Local\Python\bin\python.exe"
)

foreach ($p in $PythonPaths) {
    if ($p -and (Test-Path $p) -and ($p -notlike "*WindowsApps*")) {
        $PythonCmd = $p
        break
    }
}

if (-not $PythonCmd) {
    Write-Host "  [ERROR] Python not found. Install Python 3.10+ and try again." -ForegroundColor Red
    exit 1
}

Write-Host "  Using Python: $PythonCmd" -ForegroundColor DarkGray

$ReqFile = Join-Path $ScriptDir "requirements.txt"
if (Test-Path $ReqFile) {
    Write-Host "  Checking dependencies..." -ForegroundColor Gray
    & $PythonCmd -m pip install -r $ReqFile -q 2>$null
}

$Apps = @(
    @{ Name = "Contoso HR Portal";    Config = "configs\config-contoso.yaml";    Port = 5010 },
    @{ Name = "Fabrikam Wiki";        Config = "configs\config-fabrikam.yaml";   Port = 5011 },
    @{ Name = "Woodgrove Ticketing";  Config = "configs\config-woodgrove.yaml";  Port = 5012 }
)

Write-Host ""
Write-Host "  ============================================================" -ForegroundColor DarkCyan
Write-Host "    SSO + SCIM Service Provider Lab - Starting All Apps" -ForegroundColor White
Write-Host "  ============================================================" -ForegroundColor DarkCyan
Write-Host ""

$AppScript = Join-Path $ScriptDir "app.py"
$Processes = @()

foreach ($app in $Apps) {
    $cfgPath = Join-Path $ScriptDir $app.Config
    if (-not (Test-Path $cfgPath)) {
        Write-Host "  [WARN] Config not found: $cfgPath" -ForegroundColor Yellow
        continue
    }

    Write-Host "  Starting $($app.Name) on port $($app.Port)..." -ForegroundColor Cyan

    $proc = Start-Process -FilePath $PythonCmd -ArgumentList "`"$AppScript`" --config `"$cfgPath`"" -WorkingDirectory $ScriptDir -WindowStyle Normal -PassThru

    $Processes += @{ Name = $app.Name; Process = $proc; Port = $app.Port }
}

Start-Sleep -Seconds 3

Write-Host ""
Write-Host "  ------------------------------------------------------------" -ForegroundColor DarkGray

foreach ($p in $Processes) {
    if (-not $p.Process.HasExited) {
        Write-Host "  [OK]   $($p.Name)" -ForegroundColor Green
        Write-Host "         Dashboard:  http://localhost:$($p.Port)/dashboard" -ForegroundColor Gray
        Write-Host "         SSO Login:  http://localhost:$($p.Port)/login" -ForegroundColor Gray
    }
    else {
        Write-Host "  [FAIL] $($p.Name) - process exited" -ForegroundColor Red
    }
}

Write-Host "  ------------------------------------------------------------" -ForegroundColor DarkGray
Write-Host ""

if (-not $NoBrowser) {
    foreach ($p in $Processes) {
        if (-not $p.Process.HasExited) {
            Start-Process "http://localhost:$($p.Port)/dashboard"
        }
    }
    Write-Host "  Dashboards opened in browser." -ForegroundColor Gray
}

Write-Host ""
Write-Host "  All apps running. To stop them:" -ForegroundColor Gray
Write-Host "    - Close the individual console windows, or" -ForegroundColor Gray
Write-Host "    - Run: Get-Process python* | Stop-Process" -ForegroundColor Gray
Write-Host ""
