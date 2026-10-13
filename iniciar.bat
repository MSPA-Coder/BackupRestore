@echo off
REM Sobe a interface do BackupRestore e abre no navegador.
REM Feche esta janela para parar o servidor.
setlocal
cd /d "%~dp0"

if defined BACKUPRESTORE_PYTHON (
    set "PY=%BACKUPRESTORE_PYTHON%"
) else if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY=%~dp0.venv\Scripts\python.exe"
) else (
    set "PY=%LOCALAPPDATA%\Python\bin\python.cmd"
)

"%PY%" -c "import flask, sys; raise SystemExit(0 if sys.version_info >= (3, 13) else 1)" >nul 2>&1
if errorlevel 1 (
    echo ERRO: Python 3.15 com Flask nao foi encontrado.
    echo Recrie .venv com Python 3.15 ou defina BACKUPRESTORE_PYTHON.
    pause
    exit /b 1
)

start "" http://127.0.0.1:5401/
"%PY%" web.py
pause
