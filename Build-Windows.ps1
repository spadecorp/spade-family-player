param([Parameter(Mandatory=$true)][string]$FFmpegBin)
$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
try {
    $ff = (Resolve-Path (Join-Path $FFmpegBin "ffmpeg.exe")).Path
    $probe = (Resolve-Path (Join-Path $FFmpegBin "ffprobe.exe")).Path
    function RunChecked([string]$Program, [string[]]$Arguments) {
        & $Program @Arguments
        if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
    }
    RunChecked "py" @("-3.11","-m","venv",".build-env")
    $python = ".\.build-env\Scripts\python.exe"
    RunChecked $python @("-m","pip","install","pyinstaller>=6.11,<7")
    RunChecked $python @("-m","PyInstaller","--noconfirm","--clean","--onedir","--windowed","--name","SPaDe-Family-Player",
        "--add-binary","${ff};.","--add-binary","${probe};.","--add-data","client\player.html;.",
        "--add-data","client\settings.js;.","--add-data","client\player.js;.","--add-data","client\player.css;.","client\player.py")
    Compress-Archive -Path "dist\SPaDe-Family-Player" -DestinationPath "dist\SPaDe-Family-Player-Portable.zip" -Force
    Write-Host "Viewer package: dist\SPaDe-Family-Player-Portable.zip"
} finally { Pop-Location }
