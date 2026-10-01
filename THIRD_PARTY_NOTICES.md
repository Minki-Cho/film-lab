# 구성 요소 및 라이선스

배포 폴더의 `licenses/components.json`에 설치된 버전과 라이선스 원문 위치를 기록합니다. `tools/collect_licenses.py`가 설치 패키지에 포함된 원문을 복사합니다.

| 구성 요소 | 용도 | 배포 패키지의 라이선스 |
|---|---|---|
| Python | 런타임 | PSF |
| NumPy | 수치 연산 | BSD 및 포함된 수치 라이브러리 라이선스 |
| Pillow / LittleCMS | 이미지 입출력, ICC | HPND / MIT |
| PySide6 / Shiboken6 / Qt | 데스크톱 화면 | LGPLv3 및 패키지에 명시된 대안·제3자 조건 |
| rawpy | LibRaw Python 연결 | MIT |
| LibRaw | RAW 현상 | LGPL-2.1 / CDDL 선택 라이선스 및 포함 구성 요소 고지 |
| tifffile | 16비트 TIFF | BSD-3-Clause |
| ExifRead | 허용된 EXIF 정보 읽기 | BSD-3-Clause |
| PyInstaller | 배포 빌드 | GPL 및 배포물 예외 |
| 자체 초기 스타일·LUT | 필름 느낌 표현 | MIT, 프로필 폴더 원문 참조 |

Qt 및 LibRaw는 `_internal`의 공유 라이브러리로 배포됩니다. 해당 라이브러리를 수정·교체할 수 있도록 폴더 형태로 유지합니다. LGPL 라이브러리 변경 사항을 디버깅하기 위한 역공학을 제한하지 않습니다. 앱 소스와 재현 빌드 절차를 함께 제공합니다.

상위 프로젝트 및 소스: [Python](https://www.python.org/), [NumPy](https://github.com/numpy/numpy), [Pillow](https://github.com/python-pillow/Pillow), [Qt for Python](https://code.qt.io/cgit/pyside/pyside-setup.git/), [Qt](https://code.qt.io/), [rawpy](https://github.com/letmaik/rawpy), [LibRaw](https://github.com/LibRaw/LibRaw), [tifffile](https://github.com/cgohlke/tifffile), [ExifRead](https://github.com/ianare/exif-py), [PyInstaller](https://github.com/pyinstaller/pyinstaller).

포함된 스타일과 LUT는 필름 느낌을 표현하기 위해 자체 제작했습니다. 본 앱은 독립적으로 개발한 프로젝트입니다.

테스트에 사용하는 공개 사진은 해당 사진 저작권자의 저작물입니다. 테스트 데이터와 그 변환 결과는 앱 배포본에 포함하지 않습니다.
