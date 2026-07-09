#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Manage the ZKTeco Auto Attend Windows Service.

.PARAMETER Action
    install   Install dependencies, register, and start the service (default)
    start     Start an already-registered service
    stop      Stop the running service
    restart   Stop then start the service
    remove    Stop and unregister the service completely
    status    Show current service status

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File auto_attend\install_service.ps1
    powershell -ExecutionPolicy Bypass -File auto_attend\install_service.ps1 -Action restart
    powershell -ExecutionPolicy Bypass -File auto_attend\install_service.ps1 -Action remove
#>

param (
    [ValidateSet("install","start","stop","restart","remove","status")]
    [string]$Action = "install"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ServiceName   = "ZKTecoAutoAttend"
$ProjectDir    = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ServiceScript = Join-Path $ProjectDir "auto_attend\service_windows.py"
$EnvFile       = Join-Path $ProjectDir ".env"
$EnvExample    = Join-Path $ProjectDir ".env.example"
$ConfigFile    = Join-Path $ProjectDir "auto_attend_config.json"
$ConfigExample = Join-Path $ProjectDir "auto_attend_config.json.example"

# ── Find Python 3.10+ ─────────────────────────────────────────────────────────
function Get-PythonExe {
    $names = @("python", "python3")
    foreach ($n in $names) {
        $cmd = Get-Command $n -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $ver = & $cmd.Source --version 2>&1
        if ($ver -match "Python 3\.(\d+)" -and [int]$Matches[1] -ge 10) {
            return $cmd.Source
        }
    }
    return $null
}

$PythonExe = Get-PythonExe
if (-not $PythonExe) {
    Write-Error "Python 3.10+ not found. Install Python and ensure it is on PATH."
    exit 1
}
Write-Host "Python   : $PythonExe" -ForegroundColor Cyan
Write-Host "Project  : $ProjectDir" -ForegroundColor Cyan

# ── Helpers ───────────────────────────────────────────────────────────────────
function Show-Status {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svc) {
        $wmi = Get-WmiObject Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
        Write-Host "`nService : $($svc.DisplayName)" -ForegroundColor Cyan
        Write-Host "Status  : $($svc.Status)"
        if ($wmi) { Write-Host "Startup : $($wmi.StartMode)" }
    } else {
        Write-Host "`nService '$ServiceName' is not installed." -ForegroundColor Gray
    }
}

function Stop-ServiceNow {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -ne "Stopped") {
        Write-Host "Stopping service..." -ForegroundColor Yellow
        Stop-Service -Name $ServiceName -Force
        Write-Host "Service stopped." -ForegroundColor Green
    }
}

function Start-ServiceNow {
    Write-Host "Starting service..." -ForegroundColor Yellow
    Start-Service -Name $ServiceName
    Write-Host "Service started." -ForegroundColor Green
    Show-Status
}

# ── Actions ───────────────────────────────────────────────────────────────────
function Invoke-Install {
    Write-Host "`n=== Step 1: Install Python dependencies ===" -ForegroundColor Yellow
    & $PythonExe -m pip install -r (Join-Path $ProjectDir "requirements.txt") --quiet
    if ($LASTEXITCODE -ne 0) { throw "pip install failed." }
    Write-Host "Dependencies installed." -ForegroundColor Green

    # Run pywin32 post-install
    Write-Host "`n=== Step 2: pywin32 post-install ===" -ForegroundColor Yellow
    $pyScripts = & $PythonExe -c "import sys; print(sys.prefix + r'\Scripts')" 2>&1
    $postInstall = Join-Path $pyScripts "pywin32_postinstall.py"
    if (Test-Path $postInstall) {
        & $PythonExe $postInstall -install 2>&1 | Out-Null
        Write-Host "pywin32 post-install complete." -ForegroundColor Green
    } else {
        Write-Host "pywin32 post-install script not found — skipping." -ForegroundColor Gray
    }

    # Ensure .env exists
    Write-Host "`n=== Step 3: Check .env configuration ===" -ForegroundColor Yellow
    if (-not (Test-Path $EnvFile)) {
        if (Test-Path $EnvExample) {
            Copy-Item $EnvExample $EnvFile
            Write-Warning ".env was missing — copied from .env.example."
        } else {
            Write-Warning ".env and .env.example not found. Using defaults."
        }
    } else {
        Write-Host ".env found." -ForegroundColor Green
    }

    # Ensure config file exists
    Write-Host "`n=== Step 4: Check auto_attend_config.json ===" -ForegroundColor Yellow
    if (-not (Test-Path $ConfigFile)) {
        if (Test-Path $ConfigExample) {
            Copy-Item $ConfigExample $ConfigFile
            Write-Warning "auto_attend_config.json was missing — copied from example."
            Write-Warning "IMPORTANT: Edit $ConfigFile and set user_id, device_ids before starting."
        } else {
            Write-Warning "Config file and example not found. Using DB table defaults."
        }
    } else {
        Write-Host "auto_attend_config.json found." -ForegroundColor Green
    }

    # Remove existing service if present
    $existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "`nExisting service found — removing first..." -ForegroundColor Yellow
        Stop-ServiceNow
        & $PythonExe $ServiceScript remove | Out-Null
    }

    Write-Host "`n=== Step 5: Register Windows Service ===" -ForegroundColor Yellow
    & $PythonExe $ServiceScript install
    if ($LASTEXITCODE -ne 0) { throw "Service registration failed." }

    Set-Service -Name $ServiceName -StartupType Automatic
    Write-Host "Startup type: Automatic" -ForegroundColor Green

    Write-Host "`n=== Step 6: Start service ===" -ForegroundColor Yellow
    Start-ServiceNow

    Write-Host "`n=== Installation complete ===" -ForegroundColor Green
    Write-Host "Service   : $ServiceName"
    Write-Host "Config    : auto_attend_config.json"
    Write-Host "Logs      : $ProjectDir\logs\auto_attend.log"
}

function Invoke-Remove {
    Stop-ServiceNow
    Write-Host "Removing service..." -ForegroundColor Yellow
    & $PythonExe $ServiceScript remove
    Write-Host "Service removed." -ForegroundColor Green
}

switch ($Action) {
    "install" { Invoke-Install }
    "start"   { Start-ServiceNow }
    "stop"    { Stop-ServiceNow }
    "restart" { Stop-ServiceNow; Start-ServiceNow }
    "remove"  { Invoke-Remove }
    "status"  { Show-Status }
}
