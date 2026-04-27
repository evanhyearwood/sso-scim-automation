<#
.SYNOPSIS
    Launches all three SCIM Service Provider apps simultaneously.

.DESCRIPTION
    Starts Contoso HR Portal (5010), Fabrikam Wiki (5011), and
    Woodgrove Ticketing (5012) in separate console windows.
    Optionally opens all dashboards in the default browser.

.PARAMETER NoBrowser
    Skip opening dashboards in the browser.

.PARAMETER CreateShortcut
    Creates a desktop shortcut to launch all apps with one click.

.EXAMPLE
    .\Start-SCIMApps.ps1
    .\Start-SCIMApps.ps1 -CreateShortcut
    .\Start-SCIMApps.ps1 -NoBrowser

.NOTES
    Developed for SCIP by Evan H. Yearwood
#>

param(
    [switch]$NoBrowser,
    [switch]$CreateShortcut
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

# ── Create Desktop Shortcut ──────────────────────────────────────────
if ($CreateShortcut) {
    $Desktop = [Environment]::GetFolderPath("Desktop")
    $ShortcutPath = Join-Path $Desktop "SCIM Lab Apps.lnk"
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = "powershell.exe"
    $Shortcut.Arguments = "-ExecutionPolicy Bypass -NoExit -File `"$ScriptDir\Start-SCIMApps.ps1`""
    $Shortcut.WorkingDirectory = $ScriptDir
    $Shortcut.Description = "Start all SCIM Service Provider apps for IAM lab"
    $Shortcut.IconLocation = "powershell.exe,0"
    $Shortcut.Save()
    Write-Host ""
    Write-Host "  Desktop shortcut created: $ShortcutPath" -ForegroundColor Green
    Write-Host "  Double-click 'SCIM Lab Apps' on your desktop to launch all apps." -ForegroundColor Gray
    Write-Host ""
    return
}

# ── Pre-flight Checks ────────────────────────────────────────────────
$PythonCmd = if (Get-Command python -ErrorAction SilentlyContinue) { "python" }
             elseif (Get-Command python3 -ErrorAction SilentlyContinue) { "python3" }
             else { $null }

if (-not $PythonCmd) {
    Write-Host "  [ERROR] Python not found. Install Python 3.10+ and try again." -ForegroundColor Red
    exit 1
}

# Check dependencies
$ReqFile = Join-Path $ScriptDir "requirements.txt"
if (Test-Path $ReqFile) {
    Write-Host "  Checking dependencies..." -ForegroundColor Gray
    & $PythonCmd -m pip install -r $ReqFile -q 2>$null
}

# ── App Definitions ──────────────────────────────────────────────────
$Apps = @(
    @{
        Name   = "Contoso HR Portal"
        Config = Join-Path $ScriptDir "configs\config-contoso.yaml"
        Port   = 5010
        Color  = "Cyan"
    },
    @{
        Name   = "Fabrikam Wiki"
        Config = Join-Path $ScriptDir "configs\config-fabrikam.yaml"
        Port   = 5011
        Color  = "Magenta"
    },
    @{
        Name   = "Woodgrove Ticketing"
        Config = Join-Path $ScriptDir "configs\config-woodgrove.yaml"
        Port   = 5012
        Color  = "Yellow"
    }
)

# ── Banner ───────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host "    SCIM Service Provider Lab — Starting All Apps" -ForegroundColor White
Write-Host "    SCIP — Skills, Coaching, Identity, Purpose" -ForegroundColor Gray
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host ""

# ── Launch Apps ──────────────────────────────────────────────────────
$AppScript = Join-Path $ScriptDir "app.py"
$Processes = @()

foreach ($app in $Apps) {
    $configPath = $app.Config
    if (-not (Test-Path $configPath)) {
        Write-Host "  [WARN] Config not found: $configPath" -ForegroundColor Yellow
        continue
    }

    Write-Host "  Starting $($app.Name) on port $($app.Port)..." -ForegroundColor $app.Color

    $proc = Start-Process -FilePath $PythonCmd `
        -ArgumentList "`"$AppScript`" --config `"$configPath`"" `
        -WorkingDirectory $ScriptDir `
        -WindowStyle Normal `
        -PassThru

    $Processes += @{ Name = $app.Name; Process = $proc; Port = $app.Port }
}

Start-Sleep -Seconds 2

# ── Status Check ─────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ────────────────────────────────────────────────────────────────" -ForegroundColor DarkGray

foreach ($p in $Processes) {
    if (-not $p.Process.HasExited) {
        Write-Host "  [OK]   $($p.Name) — http://localhost:$($p.Port)/dashboard" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] $($p.Name) — process exited" -ForegroundColor Red
    }
}

Write-Host "  ────────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host ""

# ── Open Dashboards ──────────────────────────────────────────────────
if (-not $NoBrowser) {
    foreach ($p in $Processes) {
        if (-not $p.Process.HasExited) {
            Start-Process "http://localhost:$($p.Port)/dashboard"
        }
    }
    Write-Host "  Dashboards opened in browser." -ForegroundColor Gray
}

# ── Instructions ─────────────────────────────────────────────────────
Write-Host ""
Write-Host "  All apps running. To stop them:" -ForegroundColor Gray
Write-Host "    - Close the individual console windows, or" -ForegroundColor Gray
Write-Host "    - Run: Get-Process python* | Stop-Process" -ForegroundColor Gray
Write-Host ""
Write-Host "  To create a desktop shortcut:" -ForegroundColor Gray
Write-Host "    .\Start-SCIMApps.ps1 -CreateShortcut" -ForegroundColor Gray
Write-Host ""
