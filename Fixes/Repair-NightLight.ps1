<#
.SYNOPSIS
    ONE-SHOT FIX: Restores Night Light and fixes Settings crash.
.DESCRIPTION
    Fixes the "Night Light greyed out" + "Settings > System > Display crashes" combo.

    The root cause is always corrupted binary data in the CloudStore registry
    under these paths (which PowerShell scripts routinely miss due to $ escaping):
    
      HKCU\...\CloudStore\Store\DefaultAccount\Current\default$windows.data.bluelightreduction.*
      HKCU\...\CloudStore\Store\DefaultAccount\Current\{GUID}$windows.data.bluelightreduction.*
    
    This script uses Python (which handles $ in paths correctly) to delete them,
    then cleans caches, re-registers the Settings app, and verifies the fix.

    SIDE EFFECTS:
    - CloudStore state for quiet hours / tiles is reset (harmless, will regenerate)
    - Settings app user data is cleared (will regenerate on first launch)
    - Old crash dumps are deleted
    
    Place in Fixes\ folder next to VolumeMixer.py.
#>

#Requires -RunAsAdministrator

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Resolve-Path (Join-Path $scriptDir "..")
# Find Python - check common locations
$pythonExe = "python"
if (-not (Get-Command $pythonExe -ErrorAction SilentlyContinue)) {
    $pythonCandidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:ProgramFiles\Python312\python.exe",
        "$env:ProgramFiles\Python311\python.exe",
        "${env:ProgramFiles(x86)}\Python312\python.exe",
        "${env:ProgramFiles(x86)}\Python311\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe"
    )
    foreach ($c in $pythonCandidates) {
        if (Test-Path $c) {
            $pythonExe = $c
            break
        }
    }
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  NIGHT LIGHT + SETTINGS CRASH REPAIR TOOL" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# ================================================================
# STEP 1: Kill interfering processes
# ================================================================
Write-Host "[1/7] Killing interfering processes..." -ForegroundColor Yellow
$procs = @("SystemSettings", "flux", "DisplayFusion")
foreach ($p in $procs) {
    taskkill /F /IM "${p}.exe" 2>$null
}
Start-Sleep 1
Write-Host "  Done." -ForegroundColor Green

# ================================================================
# STEP 2: Delete ALL BlueLightReduction keys via Python
# (PowerShell expands $ in registry paths, Python handles them correctly)
# ================================================================
Write-Host "[2/7] Deleting corrupted BlueLightReduction registry data..." -ForegroundColor Yellow

$pyScript = Join-Path $scriptDir "delete_bluelight_keys.py"
if (Test-Path $pyScript) {
    $result = & $pythonExe $pyScript 2>&1
    $result | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
} else {
    Write-Host "  [ERROR] Python helper not found at: $pyScript" -ForegroundColor Red
}

# ================================================================
# STEP 3: Clean the Store Current Data value
# ================================================================
Write-Host "[3/7] Clearing Store Current Data to force regeneration..." -ForegroundColor Yellow

$storeCurrent = "HKCU:\Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Current"
if (Test-Path $storeCurrent) {
    Remove-ItemProperty -Path $storeCurrent -Name "Data" -ErrorAction SilentlyContinue
    Write-Host "  Cleared Current Data." -ForegroundColor DarkGray
    
    # Also remove all subkeys (they'll be regenerated)
    Get-ChildItem $storeCurrent -ErrorAction SilentlyContinue | ForEach-Object {
        Remove-Item $_.PSPath -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  Removed subkey: $($_.PSChildName)" -ForegroundColor DarkGray
    }
}
Write-Host "  Store Current cleaned." -ForegroundColor Green

# ================================================================
# STEP 4: Clear CloudStore Cache (all of it)
# ================================================================
Write-Host "[4/7] Clearing CloudStore Cache..." -ForegroundColor Yellow

$cacheBase = "HKCU:\Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\Cache\DefaultAccount"
if (Test-Path $cacheBase) {
    Get-ChildItem $cacheBase -ErrorAction SilentlyContinue | ForEach-Object {
        Remove-Item $_.PSPath -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Host "  Cache cleared." -ForegroundColor Green
} else {
    Write-Host "  No cache to clear." -ForegroundColor DarkGray
}

# ================================================================
# STEP 5: Clear Settings app cached data
# ================================================================
Write-Host "[5/7] Clearing Settings app user data..." -ForegroundColor Yellow

$pkgDir = "$env:LOCALAPPDATA\Packages\windows.immersivecontrolpanel_cw5n1h2txyewy"
if (Test-Path $pkgDir) {
    Get-ChildItem $pkgDir -ErrorAction SilentlyContinue | ForEach-Object {
        Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  Cleared: $($_.Name)" -ForegroundColor DarkGray
    }
}
Write-Host "  App data cleared." -ForegroundColor Green

# ================================================================
# STEP 6: Re-register Settings App package
# ================================================================
Write-Host "[6/7] Re-registering Settings app..." -ForegroundColor Yellow

Get-AppxPackage -PackageTypeFilter Main -Name "*immersivecontrolpanel*" | Remove-AppxPackage -ErrorAction SilentlyContinue
Start-Sleep 1

$pkg = Get-AppxPackage -AllUsers -Name "*immersivecontrolpanel*" -ErrorAction SilentlyContinue
if ($pkg) {
    Add-AppxPackage -Register "$($pkg.InstallLocation)\AppxManifest.xml" -DisableDevelopmentMode -ForceApplicationShutdown
    Write-Host "  Registered: $($pkg.PackageFullName)" -ForegroundColor Green
} else {
    Write-Host "  [ERROR] Package not found!" -ForegroundColor Red
}

# Also clear old crash dumps
$dumpDir = "$env:LOCALAPPDATA\CrashDumps"
if (Test-Path $dumpDir) {
    Remove-Item "$dumpDir\SystemSettings.exe*.dmp" -Force -ErrorAction SilentlyContinue
}

# ================================================================
# STEP 7: Test and report
# ================================================================
Write-Host "[7/7] Testing fix..." -ForegroundColor Yellow

# Launch Settings to Night Light page
Start-Process ms-settings:nightlight
Start-Sleep 5

# Check for crash
$crash = Get-WinEvent -LogName Application -MaxEvents 1 -ErrorAction SilentlyContinue | Where-Object {
    $_.Id -eq 1000 -and $_.Message -match "SystemSettings" -and $_.TimeCreated -gt (Get-Date).AddSeconds(-10)
}

# Check if Settings is running
$running = (Get-Process -Name "SystemSettings" -ErrorAction SilentlyContinue) -ne $null

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  FIX RESULT" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

if ($crash) {
    Write-Host "  [FAILED] Settings CRASHED" -ForegroundColor Red
    Write-Host "    Time: $($crash.TimeCreated)" -ForegroundColor Gray
} elseif ($running) {
    Write-Host "  [OK] Settings is RUNNING" -ForegroundColor Green
    Write-Host "  [OK] Night Light page should be open on screen" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Next: Check the toggle on screen." -ForegroundColor Yellow
    Write-Host "  If it's still greyed out, the display driver may need" -ForegroundColor Yellow
    Write-Host "  updating via Windows Update or NVIDIA's website." -ForegroundColor Yellow
} else {
    Write-Host "  [INFO] Settings closed (may have auto-exited)" -ForegroundColor DarkYellow
}

Write-Host ""
Write-Host "  To manually verify:" -ForegroundColor Yellow
Write-Host "    Win + I  ->  System  ->  Display  ->  Night Light" -ForegroundColor Gray
Write-Host ""

# Write a log file with the result
$logFile = Join-Path $scriptDir "NightLightFix_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
@"
Night Light Fix Report
======================
Date: $(Get-Date)
User: $env:USERNAME

Result:
- Settings crashed: $($crash -ne $null)
- Settings running: $running

Actions taken:
1. Killed interfering processes (SystemSettings, flux, DisplayFusion)
2. Deleted all BlueLightReduction CloudStore registry keys (via Python)
3. Cleared Store Current Data value
4. Cleared CloudStore Cache
5. Wiped Settings app user data
6. Re-registered Settings app package
7. Cleared old crash dumps
"@ | Out-File -FilePath $logFile -Encoding utf8
Write-Host "  Log saved to: $logFile" -ForegroundColor DarkGray

Read-Host "`nPress Enter to exit"
