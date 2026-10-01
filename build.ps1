$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 x64가 필요합니다.' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw '의존성 설치 실패' }
& '.\.venv\Scripts\python.exe' -m pytest
if ($LASTEXITCODE -ne 0) { throw '검증 실패: 빌드를 중단합니다.' }
& '.\.venv\Scripts\python.exe' -m PyInstaller --noconfirm FilmLab.spec
if ($LASTEXITCODE -ne 0) { throw '빌드 실패' }
& '.\.venv\Scripts\python.exe' tools\collect_licenses.py
Copy-Item -LiteralPath 'README.md' -Destination 'dist\FilmLab\사용 안내.md'
Copy-Item -LiteralPath 'requirements-lock.txt' -Destination 'dist\FilmLab\requirements-lock.txt'
Copy-Item -LiteralPath 'THIRD_PARTY_NOTICES.md' -Destination 'dist\FilmLab\THIRD_PARTY_NOTICES.md'
Copy-Item -LiteralPath 'docs' -Destination 'dist\FilmLab\docs' -Recurse -Force
Copy-Item -LiteralPath 'schemas' -Destination 'dist\FilmLab\schemas' -Recurse -Force
Write-Host '완료: dist\FilmLab\FilmLab.exe'
