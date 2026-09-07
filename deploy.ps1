[CmdletBinding()]
param(
    [switch]$SkipBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

function Get-PythonCommand {
    foreach ($candidate in @("python", "py")) {
        if (Get-Command $candidate -ErrorAction SilentlyContinue) {
            return $candidate
        }
    }
    throw "未找到 Python，请先安装 Python 3.10 或更高版本并加入 PATH。"
}

$PythonCommand = Get-PythonCommand
$PythonArgs = @()
if ($PythonCommand -eq "py") {
    $PythonArgs += "-3"
}

Write-Host "检查 Python 版本..."
$VersionText = & $PythonCommand @PythonArgs -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or -not $VersionText) {
    throw "无法读取 Python 版本。"
}
$VersionParts = $VersionText.Trim() -split "\."
$Major = [int]$VersionParts[0]
$Minor = [int]$VersionParts[1]
if ($Major -lt 3 -or ($Major -eq 3 -and $Minor -lt 10)) {
    throw "需要 Python 3.10 或更高版本，当前为 $Major.$Minor。"
}

$VenvDir = Join-Path $Root ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Host "创建虚拟环境..."
    & $PythonCommand @PythonArgs -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        throw "创建虚拟环境失败。"
    }
}

Write-Host "安装项目依赖..."
& $VenvPython -m pip install -e ".[browser]"
if ($LASTEXITCODE -ne 0) {
    throw "安装项目依赖失败。"
}

$ChromePaths = @(
    (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
    (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
)
$ChromeFound = $false
foreach ($path in $ChromePaths) {
    if (Test-Path -LiteralPath $path) {
        $ChromeFound = $true
        break
    }
}

if (-not $SkipBrowser -and -not $ChromeFound) {
    Write-Host "未检测到 Chrome，安装 Playwright Chromium..."
    & $VenvPython -m playwright install chromium
    if ($LASTEXITCODE -ne 0) {
        throw "安装 Playwright Chromium 失败。"
    }
}

Write-Host "初始化数据库..."
& $VenvPython -X utf8 news_crawler.py init
if ($LASTEXITCODE -ne 0) {
    throw "初始化数据库失败。"
}

Write-Host "运行冒烟测试..."
& $VenvPython -X utf8 smoke_test.py
if ($LASTEXITCODE -ne 0) {
    throw "冒烟测试失败。"
}

Write-Host ""
Write-Host "部署完成。运行示例："
Write-Host "  .\.venv\Scripts\python.exe -X utf8 news_crawler.py run --config sources.local.json --days 3"
