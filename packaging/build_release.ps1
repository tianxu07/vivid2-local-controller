param(
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"

if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Version must use numeric major.minor.patch form."
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$projectPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$oneFolder = Join-Path $projectRoot "dist-release\Vivid2Controller"
$singleSource = Join-Path $projectRoot "dist-single-release\Vivid2Controller.exe"
$releaseDirectory = Join-Path $projectRoot "release"
$zipAsset = Join-Path $releaseDirectory "Vivid2Controller-$Version-windows-x64.zip"
$singleAsset = Join-Path $releaseDirectory "Vivid2Controller-$Version-windows-x64-onefile.exe"
$checksumFile = Join-Path $releaseDirectory "SHA256SUMS.txt"

if (-not (Test-Path -LiteralPath $projectPython -PathType Leaf)) {
    throw "Create .venv and install requirements-build.txt before building a release."
}

Push-Location $projectRoot
try {
    & $projectPython -B -m unittest discover -s tests
    if ($LASTEXITCODE -ne 0) { throw "Offline tests failed." }

    & $projectPython -m PyInstaller --noconfirm --clean `
        --distpath dist-release --workpath build-release Vivid2Controller.spec
    if ($LASTEXITCODE -ne 0) { throw "One-folder build failed." }

    & $projectPython -m PyInstaller --noconfirm --clean `
        --distpath dist-single-release --workpath build-single-release `
        Vivid2Controller-onefile.spec
    if ($LASTEXITCODE -ne 0) { throw "Single-file build failed." }

    $notices = @(
        @{ Source = "README.txt"; Destination = "README.txt" },
        @{ Source = "LICENSE"; Destination = "LICENSE" },
        @{ Source = "THIRD_PARTY_LICENSES.txt"; Destination = "THIRD_PARTY_LICENSES.txt" },
        @{ Source = "packaging\PYTHON_LICENSE.txt"; Destination = "PYTHON_LICENSE.txt" },
        @{ Source = "packaging\PYINSTALLER_COPYING.txt"; Destination = "PYINSTALLER_COPYING.txt" }
    )
    foreach ($notice in $notices) {
        Copy-Item -LiteralPath (Join-Path $projectRoot $notice.Source) `
            -Destination (Join-Path $oneFolder $notice.Destination) -Force
    }

    New-Item -ItemType Directory -Path $releaseDirectory -Force | Out-Null
    if (Test-Path -LiteralPath $zipAsset) { Remove-Item -LiteralPath $zipAsset }
    Compress-Archive -LiteralPath $oneFolder -DestinationPath $zipAsset -CompressionLevel Optimal
    Copy-Item -LiteralPath $singleSource -Destination $singleAsset -Force
    Copy-Item -LiteralPath (Join-Path $projectRoot "README.txt") `
        -Destination (Join-Path $releaseDirectory "README.txt") -Force

    $zipHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipAsset).Hash
    $singleHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $singleAsset).Hash
    $hashLines = @(
        $zipHash + "  " + (Split-Path $zipAsset -Leaf)
        $singleHash + "  " + (Split-Path $singleAsset -Leaf)
    )
    [System.IO.File]::WriteAllLines($checksumFile, $hashLines)

    Write-Output "Release assets created in: $releaseDirectory"
} finally {
    Pop-Location
}
