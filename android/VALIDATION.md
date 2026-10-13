# Android 1.0.0 검증 기록

검증일: 2026-10-13. Windows에서 JDK 17, Android SDK Platform 35, Build Tools 35로 빌드했습니다.

## 완료한 검사

- Java 소스와 Android 리소스 컴파일, DEX 생성 완료.
- 기존 Python 엔진의 47가지 설정과 Java 엔진 결과 비교: **72,192 RGB 채널 모두 일치**, 최대 오차 0/255. 6개 스타일, 강도, 보정 조합, 극단값, 입자와 축소 좌표를 포함합니다.
- 기본 보정의 동일성, 입력 불변성, 입자 재현성, 취소, 잘못된 스타일, 손상된 프로필 거부 검사 통과.
- ARW IFD 파서 9개 검증 통과: 두 바이트 순서, 방향 정보, 순환 참조 종료, 잘못된 JPEG 범위, 실제 a6400 샘플 2개.
- 실제 ARW 샘플에서 478,859 / 791,817바이트의 JPEG를 추출하고 Pillow로 다시 읽었습니다. 둘 다 1616×1080 RGB 이미지입니다.
- `apksigner verify`로 APK Signature Scheme v3 서명 확인. 최소 API 28 이상에서 검증됩니다.
- `zipalign` 검사, APK ZIP CRC 검사, 매니페스트의 패키지·실행 Activity·최소/대상 SDK 확인.
- APK 자산 경로는 `/`로 통일했습니다. 포함된 프로필은 저장소 원본과 일치합니다.
- 앱의 인터넷·전체 사진 라이브러리·외부 저장소 권한 요청 없음.

산출물: `release/FilmLab-Android-1.0.0.apk`. 패키지 `com.filmlab.mobile`, versionCode 1, 최소 Android 9(API 28), target API 35. SHA-256은 APK 옆의 `.sha256` 파일을 확인하세요. 상세 빌드 기록은 `release/build-validation.txt`에 있습니다.

## 실행하지 못한 검사

**실제 휴대폰 및 에뮬레이터 실행 검증은 수행하지 못했습니다.** 공식 에뮬레이터/시스템 이미지 다운로드가 시간 초과되어 실행 환경을 준비하지 못했습니다. APK 빌드 및 서명 검증 성공은 실제 기기의 화면·파일 선택기·디코더·메모리 동작 검증을 대신하지 않습니다.

기기용 테스트 소스는 `android/device-tests`에 포함됩니다. 투명 PNG의 흰 배경 합성, sRGB, EXIF 8방향, ARW 외부 방향 정보, 축소와 확대 방지, JPEG 저장·재읽기, 실제 Activity의 비동기 미리보기를 검사하도록 작성했습니다. 이 테스트 APK는 컴파일·서명만 확인했으며 실행 결과를 통과로 기록하지 않았습니다.

연결된 테스트 기기 또는 에뮬레이터에서 직접 실행하려면:

```powershell
.\android\build.ps1
.\android\build-device-tests.ps1
.\.android-tools\platform-tools\adb.exe -s DEVICE_SERIAL install -r .\release\FilmLab-Android-1.0.0.apk
.\.android-tools\platform-tools\adb.exe -s DEVICE_SERIAL install -r .\android\build\device-tests\FilmLab-device-tests.apk
.\.android-tools\platform-tools\adb.exe -s DEVICE_SERIAL shell am instrument -w com.filmlab.mobile.tests/com.filmlab.mobile.tests.SmokeInstrumentation
```

`DEVICE_SERIAL`은 테스트하려는 기기의 `adb devices` 출력으로 대체합니다. 테스트 APK는 일반 사용자에게 배포할 파일이 아닙니다.

## 해석 범위

색 엔진 기준값 일치는 픽셀 배열에 동일한 설정을 적용했을 때의 결과입니다. 모바일은 Android 디코더, 처리 전 축소 및 8비트 입력을 사용하며 기본 입자 시드도 PC와 다르므로 전체 사진 저장물이 PC 출력과 픽셀 단위로 같다는 뜻은 아닙니다. ARW는 내장 JPEG만 편집하며 실제 RAW 현상과 16비트 TIFF는 제공하지 않습니다.
