@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=C:\Python314\python.exe
if not exist "%PY%" set PY=python

echo ============================================
echo  把依赖装进项目的 runtime\ 目录
echo  不写进系统 Python, 也不动别的东西
echo ============================================
"%PY%" -m pip install --target "%~dp0runtime" --upgrade mss Pillow

echo.
echo 可选: WGC 截帧后端 (只截目标窗口, 需要 44MB 的 opencv)
choice /c YN /m "现在装吗"
if errorlevel 2 goto done
"%PY%" -m pip install --target "%~dp0runtime" --upgrade numpy windows-capture

:done
echo.
echo 好了。回到外层文件夹，双击「启动.bat」。
pause
