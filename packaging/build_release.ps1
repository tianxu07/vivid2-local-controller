param(
    [string]$Version = "1.2.0",
    [string]$PrivatePatterns = "",
    [string]$RunName = ""
)

$ErrorActionPreference = "Stop"
if ($Version -ne "1.2.0") { throw "This builder prepares only v1.2.0; it must not overwrite older releases." }
if (-not $RunName) { $RunName = Get-Date -Format "yyyyMMdd-HHmmss-fff" }
if ($RunName -notmatch '^[A-Za-z0-9_-]+$') { throw "RunName must be a simple directory suffix." }

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$projectPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$releaseDirectory = Join-Path $projectRoot "release\v$Version-$RunName"
$workDirectory = Join-Path $projectRoot "build-v$Version-$RunName"
$oneFolder = Join-Path $releaseDirectory "onedir\ChihirosLocalController"
$singleSource = Join-Path $workDirectory "single-dist\ChihirosLocalController.exe"
$zipAsset = Join-Path $releaseDirectory "ChihirosLocalController-$Version-windows-x64.zip"
$singleAsset = Join-Path $releaseDirectory "ChihirosLocalController-$Version-windows-x64-onefile.exe"
$checksumFile = Join-Path $releaseDirectory "SHA256SUMS.txt"

if (-not (Test-Path -LiteralPath $projectPython -PathType Leaf)) {
    throw "Create .venv and install requirements-build.txt before building."
}
if ((Test-Path -LiteralPath $releaseDirectory) -or (Test-Path -LiteralPath $workDirectory)) {
    throw "Fresh output directories are required. Choose a new RunName; existing artifacts are never removed."
}
if (-not $PrivatePatterns) {
    $PrivatePatterns = Join-Path $projectRoot "config\release-private-patterns.json"
}
if (-not (Test-Path -LiteralPath $PrivatePatterns -PathType Leaf)) {
    throw "Provide a local private-patterns JSON file for the release privacy audit."
}

Push-Location $projectRoot
try {
    $declaredVersion = & $projectPython -c "from chihiros.constants import WINDOWS_APP_VERSION; print(WINDOWS_APP_VERSION)"
    if ($LASTEXITCODE -ne 0 -or $declaredVersion -ne $Version) { throw "Application version mismatch." }
    if ((& $projectPython -c "import platform; print(platform.machine())") -ne "AMD64") { throw "Use Windows x64 Python." }

    & $projectPython -B packaging/release_audit.py --source-root . --private-patterns $PrivatePatterns
    if ($LASTEXITCODE -ne 0) { throw "Public source privacy audit failed." }

    & $projectPython -B -m unittest discover -s tests
    if ($LASTEXITCODE -ne 0) { throw "Hardware-free tests failed." }

    New-Item -ItemType Directory -Path $releaseDirectory | Out-Null
    New-Item -ItemType Directory -Path $workDirectory | Out-Null

    & $projectPython -m PyInstaller --noconfirm --clean `
        --distpath (Join-Path $releaseDirectory "onedir") `
        --workpath (Join-Path $workDirectory "onedir") ChihirosLocalController.spec
    if ($LASTEXITCODE -ne 0) { throw "One-folder build failed." }

    & $projectPython -m PyInstaller --noconfirm --clean `
        --distpath (Join-Path $workDirectory "single-dist") `
        --workpath (Join-Path $workDirectory "onefile") ChihirosLocalController-onefile.spec
    if ($LASTEXITCODE -ne 0) { throw "One-file build failed." }

    $notices = @(
        @{ Source = "README.txt"; Destination = "README.txt" },
        @{ Source = "LICENSE"; Destination = "LICENSE" },
        @{ Source = "THIRD_PARTY_LICENSES.txt"; Destination = "THIRD_PARTY_LICENSES.txt" },
        @{ Source = "packaging\PYTHON_LICENSE.txt"; Destination = "PYTHON_LICENSE.txt" },
        @{ Source = "packaging\PYINSTALLER_COPYING.txt"; Destination = "PYINSTALLER_COPYING.txt" }
    )
    foreach ($notice in $notices) {
        Copy-Item -LiteralPath (Join-Path $projectRoot $notice.Source) `
            -Destination (Join-Path $oneFolder $notice.Destination)
    }
    Copy-Item -LiteralPath $singleSource -Destination $singleAsset

    & $projectPython -B packaging/release_audit.py --source-root . --private-patterns $PrivatePatterns `
        --folder $oneFolder --onefile $singleAsset
    if ($LASTEXITCODE -ne 0) { throw "Built payload privacy audit failed." }

    Compress-Archive -LiteralPath $oneFolder -DestinationPath $zipAsset -CompressionLevel Optimal
    & $projectPython -B packaging/release_audit.py --source-root . --private-patterns $PrivatePatterns `
        --folder $oneFolder --onefile $singleAsset --zip $zipAsset `
        --report (Join-Path $workDirectory "release-audit.json")
    if ($LASTEXITCODE -ne 0) { throw "Final release audit failed." }

    $hashLines = @(
        (Get-FileHash -Algorithm SHA256 -LiteralPath $zipAsset).Hash + "  " + (Split-Path $zipAsset -Leaf)
        (Get-FileHash -Algorithm SHA256 -LiteralPath $singleAsset).Hash + "  " + (Split-Path $singleAsset -Leaf)
    )
    [System.IO.File]::WriteAllLines($checksumFile, $hashLines)
    Write-Output "Local build complete. Nothing was published."
    Write-Output "Recommended EXE: $(Join-Path $oneFolder 'ChihirosLocalController.exe')"
    Write-Output "Recommended ZIP: $zipAsset"
    Write-Output "Optional EXE: $singleAsset"
    Write-Output "Checksums: $checksumFile"
} finally {
    Pop-Location
}
