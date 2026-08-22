@echo off
REM Sobe a interface do BackupRestore e abre no navegador.
REM Feche esta janela para parar o servidor.
setlocal
cd /d "%~dp0"

if defined BACKUPRESTORE_PYTHON (
    set "PY=%BACKUPRESTORE_PYTHON%"
) else if exist "%LOCALAPPDATA%\Python\bin\python.exe" (
    set "PY=%LOCALAPPDATA%\Python\bin\python.exe"
) else (
    set "PY=python"
)

"%PY%" -c "import flask, sys; raise SystemExit(0 if sys.version_info >= (3, 13) else 1)" >nul 2>&1
if errorlevel 1 (
    echo ERRO: Python 3.13 ou superior com Flask nao foi encontrado.
    echo Defina BACKUPRESTORE_PYTHON com o caminho de um runtime validado.
    pause
    exit /b 1
)

start "" http://127.0.0.1:5401/
"%PY%" web.py
pause
