# Build PDF2LIST per Windows (x64). Eseguire da PowerShell nella cartella del progetto:
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$python = "py"
try {
    & $python -3.11 --version | Out-Null
} catch {
    $python = "python"
}
$venv = ".venv-build"
if (-not (Test-Path $venv)) {
    & $python -m venv $venv
}
& "$venv\Scripts\python.exe" -m pip install --upgrade pip
& "$venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller

if (Test-Path build) { Remove-Item -Recurse -Force build }
if (Test-Path dist) { Remove-Item -Recurse -Force dist }
if (Test-Path release) { Remove-Item -Recurse -Force release }

& "$venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm packaging\pdf2list.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

New-Item -ItemType Directory -Force -Path release | Out-Null
Compress-Archive -Path "dist\PDF2LIST" -DestinationPath "release\PDF2LIST-Windows-x64.zip" -Force

Write-Host ""
Write-Host "Creato: release\PDF2LIST-Windows-x64.zip"
Write-Host "Avvio: estrai lo ZIP e lancia PDF2LIST\PDF2LIST.exe"
