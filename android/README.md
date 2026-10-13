# FILM LAB Android

Windows FILM LAB의 필름 프로필을 사용하는 **오프라인 Android 사진 편집 앱**입니다. Android 9(API 28) 이상에서 실행되며, 계정·서버·인터넷 권한이 필요하지 않습니다.

**[휴대폰에서 APK 바로 다운로드](https://github.com/Minki-Cho/film-lab/releases/download/android-v1.0.0/FilmLab-Android-1.0.0.apk)** · [GitHub 릴리스](https://github.com/Minki-Cho/film-lab/releases/tag/android-v1.0.0)

## 설치와 사용

1. 휴대폰에서 위 APK 다운로드 링크를 엽니다. PC에서 받은 경우 `FilmLab-Android-1.0.0.apk`를 휴대폰으로 복사합니다.
2. 휴대폰의 파일 앱에서 APK를 열고 설치합니다. 설치가 차단되면 해당 파일 앱의 **알 수 없는 앱 설치**를 허용한 뒤 다시 엽니다.
3. FILM LAB을 실행하고 **사진 열기**로 사진을 고릅니다.
4. 필름 스타일과 보정값을 선택하고, 비교 버튼을 누르고 있는 동안 원본을 확인합니다.
5. JPEG 저장을 눌러 크기와 저장 위치를 선택합니다. 결과는 새 파일로 저장하며 원본은 읽기만 합니다.

APK는 직접 설치용으로 서명되어 있습니다. Play 스토어에 게시하지 않았습니다. iPhone에는 APK를 설치할 수 없습니다.

## 지원 범위

| 항목 | Android 1.0.0 |
|---|---|
| 일반 사진 | JPEG, PNG, WebP, BMP 및 기기에서 디코딩 가능한 HEIC 등 |
| Sony ARW | 파일에 포함된 가장 큰 JPEG 미리보기를 추출하여 편집. 센서 RAW 현상이 아님 |
| 스타일 | 기본, 다큐멘터리, 네거티브, 시네마, 비비드, 흑백 |
| 보정 | 필름 강도, 노출, 따뜻함, 틴트, 명암, 채도, 하이라이트, 그림자, 입자 |
| 비교·취소 | 원본 누름 비교, 실행 취소, 초기화 |
| 저장 | sRGB JPEG 품질 95, 긴 변 2048 또는 3840 이하. 작은 원본은 확대하지 않음 |
| 회전·투명도 | 디코더의 EXIF 회전 적용, 투명 영역은 흰 배경에 합성 |
| 개인정보 | 서버 업로드 없음, 전체 사진 라이브러리·저장소 권한 없음. 파일 선택기로 고른 사진만 읽음 |

PC 버전의 RAW 현상, 16비트 TIFF 입출력, 여러 사진 일괄 처리, 프로젝트·레시피 JSON·CSV는 이 버전에 포함되지 않습니다. 모바일은 촬영 EXIF/GPS를 출력 JPEG에 복사하지 않습니다. 애니메이션 이미지를 열면 첫 프레임을 사용합니다. ARW 미리보기가 없거나 지원하지 않는 형식이면 오류를 안내합니다. 입자는 같은 설정에서 재현되지만 모바일의 기본 시드는 사진 파일 해시와 결합하지 않으므로 PC 출력과 다를 수 있습니다.

색 변환은 기존 버전 1.0.0의 LUT와 흑백 곡선을 사용합니다. 원본 프로필 SHA-256을 검증하고 사면체 보간, 선형 sRGB 노출·화이트밸런스·채도를 Java로 구현했습니다. 모바일에서는 축소한 8비트 이미지를 보정하므로 PC의 전체 해상도 float 처리 후 축소와 픽셀 단위로 같지는 않습니다.

## 다시 빌드

PowerShell 7에서 저장소 루트를 기준으로 실행합니다. Android Studio 설치는 필요하지 않습니다.

```powershell
.\android\setup.ps1
.\android\build.ps1
```

처음 준비할 때 공식 Google Android SDK Build Tools 35 / Platform 35와 Eclipse Temurin JDK 17을 다운로드하고 체크섬을 확인합니다. 도구는 `.android-tools`에만 압축 해제합니다. 빌드는 리소스 컴파일, Java 컴파일, 색 처리 기준값 테스트, DEX 생성, 정렬, APK 서명 및 서명 검증을 수행합니다.

업데이트 APK도 동일한 키로 서명해야 기존 앱 위에 설치할 수 있습니다. `.android-tools/film-lab-local.jks`와 `.android-tools/film-lab-local.pass`를 함께 안전하게 백업하세요. 이 파일은 Git과 APK에 포함되지 않습니다. 버전을 올리려면 `android/build.ps1 -VersionName 1.0.1 -VersionCode 2`처럼 실행합니다.

## 검증

- `android/tests/generate_references.py`: Python 엔진에서 색 처리 기준값 생성.
- `android/tests/FilmEngineTest.java`: 기존 Python 엔진과 색 변환 결과, 기본값 동일성, 잘못된 설정, 취소, 프로필 무결성 검사.
- `android/tests/ArwPreviewTest.java`: TIFF 바이트 순서, 미리보기 추출, EXIF 방향값, 잘못된 오프셋과 순환 IFD 검사.
- 최종 APK에 `apksigner verify`, `zipalign`, `aapt dump badging`을 실행합니다.

기기별 사진 제공자, HEIC 코덱, 메모리와 성능은 다를 수 있습니다. 실제 실행 검증 결과는 `android/VALIDATION.md`에 기록합니다.

공식 참고: [Android 이미지 디코더](https://developer.android.com/reference/android/graphics/ImageDecoder), [시스템 파일 선택기](https://developer.android.com/training/data-storage/shared/documents-files), [APK 서명 도구](https://developer.android.com/tools/apksigner). 필름 프로필 라이선스는 `app/src/main/assets/profiles/LICENSE.txt`에 포함됩니다.
