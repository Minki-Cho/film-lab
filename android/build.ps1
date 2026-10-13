[CmdletBinding()]
param(
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+$')]
    [string]$VersionName = '1.0.0',
    [ValidateRange(1, 2100000000)]
    [int]$VersionCode = 1
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ToolsRoot = Join-Path $RepoRoot '.android-tools'
$JavaHome = Join-Path $ToolsRoot 'jdk/jdk-17.0.20.1+1'
$BuildTools = Join-Path $ToolsRoot 'build-tools/android-15'
$AndroidJar = Join-Path $ToolsRoot 'platforms/android-35/android.jar'
$LambdaStubs = Join-Path $BuildTools 'core-lambda-stubs.jar'
$Java = Join-Path $JavaHome 'bin/java.exe'
$Javac = Join-Path $JavaHome 'bin/javac.exe'
$Aapt2 = Join-Path $BuildTools 'aapt2.exe'
$BuildRoot = Join-Path $PSScriptRoot 'build'
$ReleaseRoot = Join-Path $RepoRoot 'release'
$Apk = Join-Path $ReleaseRoot "FilmLab-Android-$VersionName.apk"

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $Executable"
    }
}

if (-not (Test-Path -LiteralPath $Javac) -or -not (Test-Path -LiteralPath $Aapt2) -or
    -not (Test-Path -LiteralPath $AndroidJar)) {
    throw 'Portable tools are missing. Run android/setup.ps1 first.'
}

# Rebuild generated files only. Never remove anything outside android/build.
if (Test-Path -LiteralPath $BuildRoot) {
    $ResolvedBuild = (Resolve-Path -LiteralPath $BuildRoot).Path
    $ExpectedBuild = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'build'))
    if ($ResolvedBuild -ne $ExpectedBuild) { throw 'Unexpected build directory.' }
    Remove-Item -LiteralPath $ResolvedBuild -Recurse -Force
}
foreach ($Directory in @('generated', 'classes', 'dex', 'test-classes', 'compiled-res')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $BuildRoot $Directory) | Out-Null
}
New-Item -ItemType Directory -Force -Path $ReleaseRoot | Out-Null

$PreviousJavaHome = $env:JAVA_HOME
$env:JAVA_HOME = $JavaHome
Push-Location $PSScriptRoot
try {
    Write-Host 'Compiling Android resources...'
    foreach ($Resource in Get-ChildItem -LiteralPath 'app/src/main/res' -File -Recurse) {
        $ResourcePath = $Resource.FullName.Substring($PSScriptRoot.Length + 1)
        Invoke-Checked $Aapt2 @('compile', $ResourcePath, '-o', 'build\compiled-res')
    }
    $CompiledResources = @(Get-ChildItem -LiteralPath 'build/compiled-res' -File |
        ForEach-Object { $_.FullName.Substring($PSScriptRoot.Length + 1) })
    $LinkArguments = @(
        'link', '-o', 'build\resources.apk', '-I', $AndroidJar,
        '--manifest', 'app\src\main\AndroidManifest.xml', '--java', 'build\generated',
        '--min-sdk-version', '28', '--target-sdk-version', '35',
        '--version-code', "$VersionCode", '--version-name', $VersionName
    ) + $CompiledResources
    Invoke-Checked $Aapt2 $LinkArguments

    # Relative ASCII source names avoid command-line limits and host path encoding.
    $Sources = @(Get-ChildItem -LiteralPath 'app/src/main/java', 'build/generated' -Filter '*.java' -Recurse |
        ForEach-Object { '"' + $_.FullName.Substring($PSScriptRoot.Length + 1).Replace('\', '/') + '"' })
    [IO.File]::WriteAllLines((Join-Path $BuildRoot 'sources.txt'), $Sources, [Text.UTF8Encoding]::new($false))
    Write-Host 'Compiling Java sources...'
    Invoke-Checked $Javac @(
        '-J-Dfile.encoding=UTF-8', '-encoding', 'UTF-8', '-source', '8', '-target', '8',
        '-bootclasspath', "$AndroidJar;$LambdaStubs", '-d', 'build/classes', '@build/sources.txt'
    )

    # Engine tests run against the desktop JVM without invoking Android framework stubs.
    $TestSources = @(Get-ChildItem -LiteralPath 'tests' -Filter '*.java' -Recurse |
        ForEach-Object { '"' + $_.FullName.Substring($PSScriptRoot.Length + 1).Replace('\', '/') + '"' })
    if ($TestSources.Count -gt 0) {
        [IO.File]::WriteAllLines((Join-Path $BuildRoot 'test-sources.txt'), $TestSources, [Text.UTF8Encoding]::new($false))
        Invoke-Checked $Javac @('-encoding', 'UTF-8', '-classpath', "build/classes;$AndroidJar", '-d', 'build/test-classes', '@build/test-sources.txt')
        $TestClasspath = "build/classes;build/test-classes;$AndroidJar"
        if (Test-Path -LiteralPath 'tests/FilmEngineTest.java') {
            Invoke-Checked $Java @('-ea', '-classpath', $TestClasspath, 'FilmEngineTest',
                '../film_lab/data/profiles', 'tests/references.tsv')
        }
        if (Test-Path -LiteralPath 'tests/ArwPreviewTest.java') {
            $RawTestArguments = @('-ea', '-classpath', $TestClasspath, 'com.filmlab.mobile.ArwPreviewTest', 'build/arw-test')
            foreach ($RawSample in @('../test-data/sony_a6400_01.arw', '../test-data/sony_a6400_03.arw')) {
                if (Test-Path -LiteralPath $RawSample) { $RawTestArguments += $RawSample }
            }
            Invoke-Checked $Java $RawTestArguments
        }
    }

    Invoke-Checked (Join-Path $JavaHome 'bin/jar.exe') @('--create', '--file', 'build/classes.jar', '-C', 'build/classes', '.')
    Write-Host 'Converting Java bytecode to Android DEX...'
    Invoke-Checked $Java @('-cp', (Join-Path $BuildTools 'lib/d8.jar'), 'com.android.tools.r8.D8',
        '--release', '--lib', $AndroidJar, '--min-api', '28', '--output', 'build/dex', 'build/classes.jar')

    Copy-Item -LiteralPath 'build/resources.apk' -Destination 'build/unsigned.apk'
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $Unsigned = Join-Path $BuildRoot 'unsigned.apk'
    $Zip = [IO.Compression.ZipFile]::Open($Unsigned, [IO.Compression.ZipArchiveMode]::Update)
    try {
        # Windows aapt2 writes backslashes in -A asset names. ZIP/Android asset
        # names must use forward slashes, so add these with explicit entry names.
        $AssetsRoot = (Resolve-Path -LiteralPath 'app/src/main/assets').Path
        foreach ($Asset in Get-ChildItem -LiteralPath $AssetsRoot -File -Recurse) {
            $EntryName = 'assets/' + $Asset.FullName.Substring($AssetsRoot.Length + 1).Replace('\', '/')
            [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($Zip, $Asset.FullName, $EntryName,
                [IO.Compression.CompressionLevel]::Optimal) | Out-Null
        }
        foreach ($Dex in Get-ChildItem -LiteralPath 'build/dex' -Filter '*.dex') {
            [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($Zip, $Dex.FullName, $Dex.Name,
                [IO.Compression.CompressionLevel]::Optimal) | Out-Null
        }
    } finally { $Zip.Dispose() }
    Invoke-Checked (Join-Path $BuildTools 'zipalign.exe') @('-f', '-p', '4', 'build/unsigned.apk', 'build/aligned.apk')

    # Keep both files private and backed up. Android updates require the same signing key.
    # These files live in the ignored tool directory and are never included in the APK.
    $KeyStore = Join-Path $ToolsRoot 'film-lab-local.jks'
    $PasswordFile = Join-Path $ToolsRoot 'film-lab-local.pass'
    if ((Test-Path -LiteralPath $KeyStore) -ne (Test-Path -LiteralPath $PasswordFile)) {
        throw 'Signing key/password mismatch. Restore the matching key files from backup.'
    }
    if (-not (Test-Path -LiteralPath $KeyStore)) {
        Write-Host 'Creating a private local release signing key...'
        $RandomBytes = New-Object byte[] 32
        $Random = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $Random.GetBytes($RandomBytes) } finally { $Random.Dispose() }
        [IO.File]::WriteAllText($PasswordFile, [Convert]::ToBase64String($RandomBytes), [Text.UTF8Encoding]::new($false))
        Invoke-Checked (Join-Path $JavaHome 'bin/keytool.exe') @(
            '-genkeypair', '-keystore', $KeyStore, '-storetype', 'JKS', '-alias', 'filmlab-local',
            '-storepass:file', $PasswordFile, '-keypass:file', $PasswordFile,
            '-keyalg', 'RSA', '-keysize', '3072', '-validity', '10000',
            '-dname', 'CN=Film Lab Local Release, O=Film Lab', '-noprompt'
        )
    }
    $ApkSigner = Join-Path $BuildTools 'lib/apksigner.jar'
    Write-Host 'Signing the release APK...'
    Invoke-Checked $Java @('-jar', $ApkSigner, 'sign', '--ks', $KeyStore, '--ks-key-alias', 'filmlab-local',
        '--ks-pass', "file:$PasswordFile",
        '--min-sdk-version', '28', '--v4-signing-enabled', 'false', '--out', $Apk, 'build/aligned.apk')
    Invoke-Checked $Java @('-jar', $ApkSigner, 'verify', '--verbose', '--print-certs', $Apk)
    Invoke-Checked (Join-Path $BuildTools 'zipalign.exe') @('-c', '-v', '4', $Apk)
    Invoke-Checked (Join-Path $BuildTools 'aapt.exe') @('dump', 'badging', $Apk)
    $Packaged = [IO.Compression.ZipFile]::OpenRead($Apk)
    $VerifiedAssets = 0
    try {
        if (@($Packaged.Entries | Where-Object { $_.FullName.Contains('\') }).Count -gt 0) {
            throw 'APK contains nonportable ZIP entry names.'
        }
        foreach ($Asset in Get-ChildItem -LiteralPath $AssetsRoot -File -Recurse) {
            $RelativeName = $Asset.FullName.Substring($AssetsRoot.Length + 1).Replace('\', '/')
            $Entry = $Packaged.GetEntry('assets/' + $RelativeName)
            if (-not $Entry) { throw "Missing APK asset: $RelativeName" }
            $Stream = $Entry.Open()
            $Sha = [Security.Cryptography.SHA256]::Create()
            try { $PackagedHash = [BitConverter]::ToString($Sha.ComputeHash($Stream)).Replace('-', '') }
            finally { $Sha.Dispose(); $Stream.Dispose() }
            $SourceHash = (Get-FileHash -LiteralPath $Asset.FullName -Algorithm SHA256).Hash
            if ($PackagedHash -ne $SourceHash) { throw "APK asset hash mismatch: $RelativeName" }
            $DesktopAsset = Join-Path $RepoRoot ('film_lab/data/' + $RelativeName)
            if ((Test-Path -LiteralPath $DesktopAsset) -and
                (Get-FileHash -LiteralPath $DesktopAsset -Algorithm SHA256).Hash -ne $PackagedHash) {
                throw "Desktop profile asset hash mismatch: $RelativeName"
            }
            $VerifiedAssets++
        }
    } finally { $Packaged.Dispose() }
    Write-Host "Verified $VerifiedAssets packaged assets against source SHA-256; all ZIP paths use forward slashes."
    $Hash = (Get-FileHash -LiteralPath $Apk -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText("$Apk.sha256", "$Hash  $([IO.Path]::GetFileName($Apk))`n", [Text.UTF8Encoding]::new($false))
    Write-Host "APK bytes: $((Get-Item -LiteralPath $Apk).Length)"
    Write-Host "APK SHA-256: $Hash"
    Write-Host "APK ready: $Apk"
} finally {
    Pop-Location
    $env:JAVA_HOME = $PreviousJavaHome
}
