@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONPATH=%~dp0runtime"
set "HERWORLD_HOME=%~dp0"
if "%HERWORLD_DATA%"=="" set "HERWORLD_DATA=%LOCALAPPDATA%\游戏搭子"
if not exist "%HERWORLD_DATA%" mkdir "%HERWORLD_DATA%"
set PY=C:\Python314\python.exe
if not exist "%PY%" set PY=python

echo ---- 自检（只拼提示词，不调用模型）----
"%PY%" -s main.py --selftest --no-call
echo.
echo ---- 正常启动（关掉这个窗口程序也会退出）----
"%PY%" -s main.py
pause
