# FILM LAB · α6400

Sony a6400의 ARW를 PC에서 현상하고 **필름 느낌**의 사진으로 저장하는 Windows 데스크톱 앱입니다. 기본 기능은 인터넷이나 계정 없이 실행됩니다.

## 빠른 시작

Windows 11 x64와 Python 3.12 x64를 사용합니다. 저장소를 복제하거나 ZIP으로 내려받은 뒤 프로젝트 폴더에서 PowerShell을 실행하세요.

```powershell
git clone https://github.com/Minki-Cho/film-lab.git
cd film-lab
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe run.py
```

의존성을 처음 설치할 때는 인터넷 연결이 필요합니다. GitHub 저장소에는 소스가 포함되며, 실행 파일은 아래 재현 빌드 절차로 생성합니다.

## 실행

빌드한 뒤에는 `dist/FilmLab/FilmLab.exe`를 실행하세요. 다른 PC에 옮길 때는 **FilmLab 폴더 전체**를 복사해야 합니다. `start.ps1`은 빌드한 실행 파일이 있으면 실행하고, 없으면 개발 환경에서 앱을 시작합니다.

1. **사진 추가**를 누르거나 ARW를 화면으로 끌어 놓습니다. 최대 100장입니다.
2. 기본 현상이 끝나면 오른쪽에서 스타일을 선택합니다.
3. 노출, 따뜻함, 틴트, 명암, 입자감을 조절합니다. 따뜻함과 틴트는 촬영 WB 기준 상대 값입니다.
4. 화면 맞춤·100%·좌우·분할 비교로 결과를 확인합니다. 비교 기준은 같은 노출과 WB로 현상한 RAW입니다.
5. 별도의 저장 폴더를 고르고 한 장·선택 사진·전체 사진을 저장합니다.

JPEG는 sRGB 8비트, TIFF는 sRGB 16비트입니다. TIFF는 현상이 완료된 RGB 이미지입니다. 원본 크기 또는 긴 변 2048/3840으로 저장하며 확대하지 않습니다. 기존 파일은 덮어쓰지 않고 순번을 붙입니다. 저장 결과마다 레시피 JSON, 일괄 처리마다 CSV가 생성됩니다.

## 편집과 프로젝트

- 스타일: 기본 현상, 다큐멘터리, 네거티브, 시네마, 비비드, 흑백.
- 실행 취소·다시 실행은 사진별 20회까지 지원합니다.
- 설정 복사는 선택한 사진에 적용합니다. 노출과 화이트밸런스는 기본으로 제외합니다.
- 프로젝트에 사진 경로와 설정을 보존합니다. 변경 후 2초 뒤 자동 저장됩니다.
- 원본이 이동했으면 재연결합니다. 크기와 SHA-256이 일치해야 합니다.
- 대기열은 저장을 누른 시점의 설정을 사용합니다. 이후 편집은 다음 저장에 적용됩니다.
- 취소는 처리 단계 경계에서 적용되므로 RAW 현상 도중에는 잠시 기다릴 수 있습니다.

원본 ARW는 읽기만 합니다. 촬영 일시·셔터·조리개·ISO·초점거리·모델을 보존하고 GPS·기기 일련번호는 저장하지 않습니다.

## 초기 스타일의 범위

현재 프로필은 필름 느낌을 표현하기 위해 자체 제작한 **초기 스타일**입니다. 통제된 참고 사진과 비교하여 색감을 보정하는 작업은 남아 있습니다. 실제 사용자 사진 20장 검증, 색차·육안 평가 및 성능 목표의 충족 여부는 [검증 기록](docs/VALIDATION.md)을 확인하세요.

## 명령행

배포본의 `film-lab-cli.exe` 또는 개발 환경의 `.venv/Scripts/python.exe -m film_lab` 뒤에 명령을 붙입니다.

```powershell
.\dist\FilmLab\film-lab-cli.exe inspect 'D:\사진\DSC01234.ARW'
.\dist\FilmLab\film-lab-cli.exe export 'D:\사진\DSC01234.ARW' --output 'D:\결과' --style baseline
.\dist\FilmLab\film-lab-cli.exe export 'D:\사진\DSC01234.ARW' --output 'D:\결과' --style documentary --format tiff
.\dist\FilmLab\film-lab-cli.exe export 'D:\사진' --output 'D:\결과' --style cinema --long-edge 2048
```

## 재현 빌드

프로젝트 폴더에서 다음을 실행합니다. `build.ps1`은 가상 환경 준비, 의존성 설치, 테스트, 실행 파일 패키징을 수행합니다.

```powershell
.\build.ps1
```

프로필은 `film_lab/data/profiles`에 버전·SHA-256·라이선스와 함께 보관합니다. `tools/generate_profiles.py`로 재생성할 수 있습니다. 실제 배포 런타임 버전은 `dist/FilmLab/licenses/components.json`에 기록합니다.

성능 측정: `.venv/Scripts/python.exe tools/benchmark.py 'D:\사진\sample.ARW' --runs 20 --export`

## 구성

- `film_lab/engine.py`: GUI와 독립된 inspect / render / export
- `film_lab/color.py`, `profiles.py`: float32 색 처리, 사면체 보간, 입자
- `film_lab/gui.py`, `worker.py`: 데스크톱 화면과 별도 작업 프로세스
- `film_lab/models.py`, `session.py`: 설정 검증과 프로젝트 보존
- `tests`: 색 처리·파일 안전성·프로젝트 회귀 검증
- `docs`: 처리 계약과 검증 기록

공식 API 근거: [rawpy Params](https://letmaik.github.io/rawpy/api/rawpy.Params.html), [LibRaw 지원 카메라](https://www.libraw.org/supported-cameras). 구성 요소 고지는 `THIRD_PARTY_NOTICES.md`와 배포본의 `licenses` 폴더를 참조하세요.
