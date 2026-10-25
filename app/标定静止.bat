@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONPATH=%~dp0runtime"
set "HERWORLD_HOME=%~dp0"
if "%HERWORLD_DATA%"=="" set "HERWORLD_DATA=%LOCALAPPDATA%\游戏搭子"
if not exist "%HERWORLD_DATA%" mkdir "%HERWORLD_DATA%"
if exist "C:\Python314\python.exe" (
  "C:\Python314\python.exe" -s "tools\calibrate_flow.py" %*
) else (
  python "tools\calibrate_flow.py" %*
)
echo.
pause
