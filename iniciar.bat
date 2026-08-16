@echo off
REM Sobe a interface do BackupRestore e abre no navegador.
REM Feche esta janela para parar o servidor.
REM
REM O caminho do Python e explicito de proposito: esta maquina tem quatro
REM instalacoes, e as duas que o PATH encontra primeiro nao servem --
REM WindowsApps\python.exe e um atalho da Loja que falha fora do Git Bash, e
REM Programs\Python\Python314 (o que o launcher `py` escolhe) nao tem Flask.
setlocal
cd /d "%~dp0"

set "PY=%LOCALAPPDATA%\Python\bin\python.exe"
if not exist "%PY%" set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY%" set "PY=python"

start "" http://127.0.0.1:5401/
"%PY%" web.py
pause
