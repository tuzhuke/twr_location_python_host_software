param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$SpecPath = Join-Path $ProjectRoot "packaging\landian_uwb_twr.spec"
$AppName = "Landian_UWB_TWR_Host_V1.0"
$DistDir = Join-Path $ProjectRoot "dist"
$ExePath = Join-Path $DistDir "$AppName.exe"
$TempExePath = Join-Path $DistDir "$AppName.exe.notanexecutable"
$OldAppDir = Join-Path $DistDir $AppName
$OldZipPath = Join-Path $DistDir "$AppName.zip"

Set-Location $ProjectRoot

Write-Host "== Landian UWB-TWR Windows packaging =="
Write-Host "Project: $ProjectRoot"

if (-not (Test-Path $SpecPath)) {
    throw "Spec file not found: $SpecPath"
}

$pythonInfo = python -c "import sys, platform; print(sys.version.split()[0]); print(platform.architecture()[0]); print(sys.executable)"
Write-Host "Python: $($pythonInfo[0]) $($pythonInfo[1])"
Write-Host "Python exe: $($pythonInfo[2])"

if ($pythonInfo[1] -ne "64bit") {
    Write-Warning "Current Python is not 64bit. Use 64bit Python 3.7 for Win7/10/11 x64 release builds."
}

if (-not $SkipInstall) {
    Write-Host "Installing build dependencies..."
    python -m pip install --upgrade pip setuptools wheel
    python -m pip install -r requirements-build-windows.txt
}

Write-Host "Stopping running packaged app if present..."
Get-CimInstance Win32_Process |
    Where-Object { $_.Name -eq "$AppName.exe" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500

Write-Host "Cleaning previous packaging outputs..."
foreach ($path in @($ExePath, $TempExePath, $OldZipPath, $OldAppDir)) {
    if (Test-Path $path) {
        Remove-Item -LiteralPath $path -Recurse -Force
    }
}

Write-Host "Running PyInstaller..."
python -m PyInstaller --noconfirm --clean $SpecPath

if (-not (Test-Path $ExePath)) {
    throw "Build failed, output exe not found: $ExePath"
}

Write-Host ""
Write-Host "Build completed."
Write-Host "EXE: $ExePath"
Write-Host ""
Write-Host "Copy this single exe to another Windows PC, then run it directly."
