# setup.ps1
# Callback setup for Windows.
# The easy way is to run setup.bat (double-click it, or type setup.bat in a terminal opened in the project folder).
# By hand:
#   powershell -ExecutionPolicy Bypass -File setup.ps1
# It installs whatever is missing, Python 3.11 and Ollama included, then the libraries and every model, and opens the app.
# Safe to run again: finished steps are skipped or simply repeated.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
# Older Windows 10 builds still default to TLS 1.0, which python.org and ollama.com refuse.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Step([string]$text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Yellow }
function Run([string]$exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "This step failed: $exe $($arguments -join ' ')" }
}
function Download([string]$url, [string]$file) {
    Write-Host "Downloading $url"
    $ProgressPreference = "SilentlyContinue"   # the progress bar makes the download many times slower
    Invoke-WebRequest -Uri $url -OutFile $file -UseBasicParsing
}

# Python 3.11 as a command: the py launcher, python on the PATH, or the folder this script installs it into. $null when there is none.
function Find-Python {
    $ErrorActionPreference = "Continue"   # a missing version is an answer here, not a failure
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.11 -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) { return ,@("py", "-3.11") }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $version = & python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($version -eq "3.11") { return ,@("python") }
    }
    $installed = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
    if (Test-Path $installed) { return ,@($installed) }
    return $null
}

function Find-Ollama {
    $command = Get-Command ollama -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $installed = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
    if (Test-Path $installed) { return $installed }
    return $null
}

function Test-Ollama {
    try { Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/version" -UseBasicParsing -TimeoutSec 2 | Out-Null; return $true }
    catch { return $false }
}

Step "Python 3.11"
$python = Find-Python
if (-not $python) {
    Write-Host "Not found, so installing it for this user (no administrator rights needed)."
    $installer = "$env:TEMP\python-3.11.9-amd64.exe"
    Download "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe" $installer
    Start-Process -Wait -FilePath $installer -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_test=0", "Include_launcher=0"
    $python = Find-Python
    if (-not $python) { throw "Python 3.11 did not install. Install it from https://www.python.org/downloads/release/python-3119/ and run this again." }
}
$pyExe = $python[0]; $pyArgs = @($python | Select-Object -Skip 1)
Write-Host "Ready."

Step "Speech and scoring environment (.venv)"
if (-not (Test-Path ".venv\Scripts\python.exe")) { Run $pyExe ($pyArgs + @("-m", "venv", ".venv")) }
$py = ".venv\Scripts\python.exe"
Run $py @("-m", "pip", "install", "--upgrade", "pip")
Run $py @("-m", "pip", "install", "setuptools==80.9.0", "wheel")
Run $py @("-m", "pip", "install", "-r", "requirements.txt", "--extra-index-url", "https://download.pytorch.org/whl/cu121")
Run $py @("-m", "pip", "install", "openai-whisper==20240930", "--no-build-isolation")

Step "Camera environment (vision_env)"
if (-not (Test-Path "vision_env\Scripts\python.exe")) { Run $pyExe ($pyArgs + @("-m", "venv", "vision_env")) }
$vpy = "vision_env\Scripts\python.exe"
Run $vpy @("-m", "pip", "install", "--upgrade", "pip")
Run $vpy @("-m", "pip", "install", "-r", "requirements-vision.txt")
Run $vpy @("-m", "pip", "install", "--no-deps", "mediapipe==1.0.1")

Step "Speech models (Whisper and the speaker model)"
Run $py @("backend\fetch_models.py")

Step "Language models (Ollama)"
$ollama = Find-Ollama
if (-not $ollama) {
    Write-Host "Ollama not found, so installing it for this user."
    $installer = "$env:TEMP\OllamaSetup.exe"
    Download "https://ollama.com/download/OllamaSetup.exe" $installer
    Start-Process -Wait -FilePath $installer -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"
    $ollama = Find-Ollama
    if (-not $ollama) { throw "Ollama did not install. Install it from https://ollama.com/download and run this again." }
}
# Models are fetched through Ollama's background service; start it if it is not running yet.
if (-not (Test-Ollama)) {
    Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
    for ($i = 0; $i -lt 30 -and -not (Test-Ollama); $i++) { Start-Sleep -Seconds 2 }
}
Run $ollama @("pull", "qwen3:4b")
Run $ollama @("pull", "qwen2.5:7b")

Step "Done"
Write-Host "Opening Callback. Next time, double-click start.bat." -ForegroundColor Green
Start-Process -FilePath "start.bat"
