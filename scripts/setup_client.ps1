param([string]$Python = "python")
$ErrorActionPreference = "Stop"
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$environmentPath = Join-Path $projectPath ".venv"
& $Python -m venv $environmentPath
if ($LASTEXITCODE -ne 0) { throw "Python environment creation failed." }
$environmentPython = Join-Path $environmentPath "Scripts\python.exe"
& $environmentPython -m pip install -e "$projectPath[quiz,semantic,desktop]"
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
& $environmentPython (Join-Path $PSScriptRoot "create_desktop_launcher.py")
if ($LASTEXITCODE -ne 0) { throw "Desktop launcher creation failed." }
Write-Host "Client environment and desktop launcher are ready."
