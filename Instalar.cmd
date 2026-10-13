@echo off
cd /d "%~dp0"
py -3.12 -m venv .venv
if errorlevel 1 (
  echo Instale Python 3.12 para Windows em https://www.python.org/downloads/windows/
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  pause
  exit /b 1
)
echo Instalacao concluida. Abra Abrir_Sala_TV.cmd.
pause
