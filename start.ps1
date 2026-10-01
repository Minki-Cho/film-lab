$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (Test-Path -LiteralPath 'dist\FilmLab\FilmLab.exe') {
    Start-Process -FilePath (Join-Path $PSScriptRoot 'dist\FilmLab\FilmLab.exe') -WindowStyle Hidden
} elseif (Test-Path -LiteralPath '.venv\Scripts\pythonw.exe') {
    Start-Process -FilePath (Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe') -ArgumentList 'run.py' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
} else {
    throw '먼저 build.ps1을 실행하세요.'
}
