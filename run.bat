@echo off
setlocal
cd /d "%~dp0"
if not exist .venv-app (
  py -3.11 -m venv .venv-app 2>nul
  if not exist .venv-app\Scripts\python.exe (
    python -m venv .venv-app
  )
  .venv-app\Scripts\python.exe -m pip install --upgrade pip
  .venv-app\Scripts\python.exe -m pip install -r requirements.txt
)
.venv-app\Scripts\pythonw.exe -m pdf2list
