@echo off
chcp 65001 >nul
cd /d "%~dp0app"

rem 程序在 app；配置、记忆、key、日志放 LOCALAPPDATA，换目录/覆盖程序也不丢
set "HERWORLD_HOME=%CD%"
set "PYTHONPATH=%CD%\runtime"
if "%HERWORLD_DATA%"=="" set "HERWORLD_DATA=%LOCALAPPDATA%\游戏搭子"
if not exist "%HERWORLD_DATA%" mkdir "%HERWORLD_DATA%"

rem API Key 在程序里「设置 - 连接」页填；也可以用 DEEPSEEK_API_KEY 环境变量

if not exist "main.py" (
  echo 没找到 app\main.py，文件夹可能不完整。
  pause
  exit /b
)

if exist "C:\Python314\pythonw.exe" (
  start "" "C:\Python314\pythonw.exe" -s "main.py"
  exit /b
)

where pythonw >nul 2>&1
if %errorlevel%==0 (
  start "" pythonw -s "main.py"
  exit /b
)

where python >nul 2>&1
if %errorlevel%==0 (
  start "" python -s "main.py"
  exit /b
)

echo 没找到 Python。请先运行 app\准备环境.bat（需要先装好 Python 3.14）。
pause
