[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ToolsRoot = Join-Path $RepoRoot '.android-tools'
$Downloads = Join-Path $ToolsRoot 'downloads'
New-Item -ItemType Directory -Force -Path $Downloads | Out-Null

# Official, pinned portable packages. Nothing is installed system-wide.
$Packages = @(
    @{
        File = 'jdk17.zip'; Algorithm = 'SHA256'
        Hash = 'e53a79c3c3d86865bd7e787903884331068e71321714ffd44f145785affc7cb0'
        Url = 'https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.20.1%2B1/OpenJDK17U-jdk_x64_windows_hotspot_17.0.20.1_1.zip'
        Directory = 'jdk'; Check = 'jdk/jdk-17.0.20.1+1/bin/javac.exe'
    },
    @{
        File = 'build-tools.zip'; Algorithm = 'SHA1'
        Hash = 'af059bb67cf7786f45ee0db85e2d24985df1b4b6'
        Url = 'https://dl.google.com/android/repository/build-tools_r35_windows.zip'
        Directory = 'build-tools'; Check = 'build-tools/android-15/aapt2.exe'
    },
    @{
        File = 'platform-35.zip'; Algorithm = 'SHA1'
        Hash = '0bb560a90a7a2cbd0dd8348224d518b638fe7949'
        Url = 'https://dl.google.com/android/repository/platform-35_r02.zip'
        Directory = 'platforms'; Check = 'platforms/android-35/android.jar'
        Entries = @('android-35/android.jar', 'android-35/source.properties', 'android-35/sdk.properties', 'android-35/build.prop')
    },
    @{
        File = 'platform-tools.zip'; Algorithm = 'SHA1'
        Hash = 'e03e78b1d80b396f1c3358e31251cb31740e1110'
        Url = 'https://dl.google.com/android/repository/platform-tools_r37.0.1-win.zip'
        Directory = '.'; Check = 'platform-tools/adb.exe'
    }
)

foreach ($Package in $Packages) {
    if (Test-Path -LiteralPath (Join-Path $ToolsRoot $Package.Check)) { continue }
    $Archive = Join-Path $Downloads $Package.File
    if (-not (Test-Path -LiteralPath $Archive)) {
        Write-Host "Downloading $($Package.File)..."
        & curl.exe --fail --location --retry 3 --output $Archive $Package.Url
        if ($LASTEXITCODE -ne 0) { throw "Download failed: $($Package.Url)" }
    }
    $Actual = (Get-FileHash -LiteralPath $Archive -Algorithm $Package.Algorithm).Hash
    if ($Actual -ne $Package.Hash) {
        throw "Checksum mismatch for $Archive. Remove this archive and rerun setup."
    }
    Write-Host "Extracting verified $($Package.File)..."
    $Destination = Join-Path $ToolsRoot $Package.Directory
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    if ($Package.Entries) {
        # aapt2/javac need android.jar, not the thousands of SDK reference assets.
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $Zip = [IO.Compression.ZipFile]::OpenRead($Archive)
        try {
            foreach ($EntryName in $Package.Entries) {
                $Entry = $Zip.GetEntry($EntryName)
                if (-not $Entry) { throw "Missing package entry: $EntryName" }
                $Output = Join-Path $Destination $EntryName
                New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Output) | Out-Null
                [IO.Compression.ZipFileExtensions]::ExtractToFile($Entry, $Output, $true)
            }
        } finally { $Zip.Dispose() }
    } else {
        Expand-Archive -LiteralPath $Archive -DestinationPath $Destination -Force
    }
}

Write-Host "Portable Android toolchain is ready in $ToolsRoot"
