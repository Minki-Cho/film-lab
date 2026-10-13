[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ToolsRoot = Join-Path $RepoRoot '.android-tools'
$JavaHome = Join-Path $ToolsRoot 'jdk/jdk-17.0.20.1+1'
$BuildTools = Join-Path $ToolsRoot 'build-tools/android-15'
$AndroidJar = Join-Path $ToolsRoot 'platforms/android-35/android.jar'
$LambdaStubs = Join-Path $BuildTools 'core-lambda-stubs.jar'
$Java = Join-Path $JavaHome 'bin/java.exe'
$TestRoot = Join-Path $PSScriptRoot 'build/device-tests'
$KeyStore = Join-Path $ToolsRoot 'film-lab-local.jks'
$PasswordFile = Join-Path $ToolsRoot 'film-lab-local.pass'

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Executable ($LASTEXITCODE)" }
}

if (-not (Test-Path -LiteralPath $KeyStore) -or -not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'build/classes'))) {
    throw 'Run android/build.ps1 successfully before building device tests.'
}
foreach ($Directory in @('classes', 'dex')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $TestRoot $Directory) | Out-Null
}
Push-Location $PSScriptRoot
try {
    $TestSources = @(Get-ChildItem -LiteralPath 'device-tests/src' -Filter '*.java' -Recurse |
        ForEach-Object { '"' + $_.FullName.Substring($PSScriptRoot.Length + 1).Replace('\', '/') + '"' })
    [IO.File]::WriteAllLines((Join-Path $TestRoot 'sources.txt'), $TestSources, [Text.UTF8Encoding]::new($false))
    Invoke-Checked (Join-Path $JavaHome 'bin/javac.exe') @('-encoding', 'UTF-8', '-source', '8', '-target', '8',
        '-bootclasspath', "$AndroidJar;$LambdaStubs", '-classpath', 'build/classes', '-d', 'build/device-tests/classes',
        '@build/device-tests/sources.txt')
    Invoke-Checked (Join-Path $JavaHome 'bin/jar.exe') @('--create', '--file', 'build/device-tests/classes.jar',
        '-C', 'build/device-tests/classes', '.')
    Invoke-Checked $Java @('-cp', (Join-Path $BuildTools 'lib/d8.jar'), 'com.android.tools.r8.D8', '--release',
        '--lib', $AndroidJar, '--classpath', 'build/classes.jar', '--min-api', '28',
        '--output', 'build/device-tests/dex', 'build/device-tests/classes.jar')
    Invoke-Checked (Join-Path $BuildTools 'aapt2.exe') @('link', '-o', 'build/device-tests/unsigned.apk',
        '-I', $AndroidJar, '--manifest', 'device-tests/AndroidManifest.xml', '--min-sdk-version', '28',
        '--target-sdk-version', '35')
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $Zip = [IO.Compression.ZipFile]::Open((Join-Path $TestRoot 'unsigned.apk'), [IO.Compression.ZipArchiveMode]::Update)
    try {
        foreach ($Dex in Get-ChildItem -LiteralPath 'build/device-tests/dex' -Filter '*.dex') {
            [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($Zip, $Dex.FullName, $Dex.Name,
                [IO.Compression.CompressionLevel]::Optimal) | Out-Null
        }
    } finally { $Zip.Dispose() }
    Invoke-Checked (Join-Path $BuildTools 'zipalign.exe') @('-f', '4', 'build/device-tests/unsigned.apk', 'build/device-tests/aligned.apk')
    $Apk = Join-Path $TestRoot 'FilmLab-device-tests.apk'
    $ApkSigner = Join-Path $BuildTools 'lib/apksigner.jar'
    Invoke-Checked $Java @('-jar', $ApkSigner, 'sign', '--ks', $KeyStore, '--ks-key-alias', 'filmlab-local',
        '--ks-pass', "file:$PasswordFile", '--min-sdk-version', '28',
        '--v4-signing-enabled', 'false', '--out', $Apk, 'build/device-tests/aligned.apk')
    Invoke-Checked $Java @('-jar', $ApkSigner, 'verify', '--verbose', $Apk)
    Write-Host "Device test APK ready: $Apk"
} finally { Pop-Location }
