# -*- coding: utf-8 -*-
#
# Build KeShare for Windows with PyInstaller (one file per executable).
#
#   .\build.ps1                 # windowed KeShare.exe
#   .\build.ps1 -Console        # console build (shows tracebacks)
#   .\build.ps1 -Python C:\Python38\python.exe
#
# The script NEVER fails because PyInstaller is missing: it prints install
# instructions and exits 0 so CI / casual builds keep working.

[CmdletBinding()]
param(
    [switch]$Console,
    [string]$Python = ""
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }

# ---------------------------------------------------------------------------
# locate a Python interpreter
# ---------------------------------------------------------------------------
if (-not $Python) {
    $candidates = @(
        "D:\deepseek-work\python312\python.exe",
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python310\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python39\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python38\python.exe")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) { $Python = $candidate; break }
    }
}
if (-not $Python) {
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) { $Python = $command.Source }
}

if (-not $Python) {
    Write-Host "未找到 Python 解释器，跳过打包。" -ForegroundColor Yellow
    Write-Host "安装 Python 3.8+ 后重新运行：.\build.ps1"
    exit 0
}

Write-Step "使用解释器: $Python"
$version = & $Python -c "import sys; print('{}.{}.{}'.format(*sys.version_info[:3]))" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "无法运行 Python ($version)，跳过打包。" -ForegroundColor Yellow
    exit 0
}
Write-Host "    Python $version"

$main = Join-Path $root "main.pyw"
if (-not (Test-Path $main)) {
    Write-Host "缺少 main.pyw，无法打包。" -ForegroundColor Yellow
    exit 0
}

# ---------------------------------------------------------------------------
# is PyInstaller available?
# ---------------------------------------------------------------------------
$hasPyInstaller = $false
$probe = & $Python -m PyInstaller --version 2>&1
if ($LASTEXITCODE -eq 0) { $hasPyInstaller = $true }

if (-not $hasPyInstaller) {
    Write-Host ""
    Write-Host "未检测到 PyInstaller，已跳过 EXE 打包（源码仍可直接运行）。" -ForegroundColor Yellow
    Write-Host "安装后重新运行本脚本即可生成单文件 EXE："
    Write-Host ""
    Write-Host "    `"$Python`" -m pip install pyinstaller"
    Write-Host "    .\build.ps1"
    Write-Host ""
    Write-Host "不安装 PyInstaller 也可以直接运行："
    Write-Host "    `"$Python`" `"$main`""
    Write-Host "    `"$Python`" `"$main`" --version"
    exit 0
}

# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
$dist = Join-Path $root "dist"
$work = Join-Path $root "build"
$spec = Join-Path $work "spec"

$mode = "--windowed"
if ($Console) { $mode = "--console" }

Write-Step "PyInstaller 打包（$mode，单文件）"
& $Python -m PyInstaller `
    --noconfirm --clean --onefile $mode `
    --name "KeShare" `
    --distpath $dist `
    --workpath $work `
    --specpath $spec `
    --hidden-import "tkinter" `
    --exclude-module "unittest" `
    --exclude-module "pydoc" `
    $main

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "PyInstaller 打包失败（退出码 $LASTEXITCODE）。源码未受影响。" -ForegroundColor Yellow
    exit 0
}

$exe = Join-Path $dist "KeShare.exe"
if (Test-Path $exe) {
    $size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Step "已生成 $exe ($size MiB)"
    exit 0
}

Write-Host "打包结束，但没有找到 $exe。" -ForegroundColor Yellow
exit 0
