@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONPATH=%~dp0runtime"
set PY=C:\Python314\python.exe
if not exist "%PY%" set PY=python

echo ============================================
echo  打包成独立 exe（提示词一起打包）
echo ============================================
"%PY%" -m pip install --target "%~dp0runtime" --upgrade pyinstaller

"%PY%" -m PyInstaller --noconfirm --clean --onedir --windowed ^
  --name HerWorld3 ^
  --distpath "dist" --workpath "build" --specpath "build" ^
  --hidden-import mss ^
  --hidden-import settings_ui ^
  --hidden-import providers ^
  --hidden-import keystore ^
  --add-data "prompt_defaults;prompt_defaults" ^
  --add-data "assets;assets" ^
  --exclude-module numpy --exclude-module cv2 ^
  --exclude-module PyInstaller --exclude-module setuptools ^
  --exclude-module pip --exclude-module pytest ^
  main.py

if not exist "dist\HerWorld3\HerWorld3.exe" (
  echo 打包失败，看上面的报错。
  pause
  exit /b
)

rem exe 只有在纯英文路径下才能启动，项目路径带中文，所以装到 LOCALAPPDATA
set "APP=%LOCALAPPDATA%\HerWorld3"
echo.
echo 部署到 %APP% ...
xcopy "dist\HerWorld3\*" "%APP%\" /E /I /Y /Q >nul

echo.
echo 完成: %APP%\HerWorld3.exe
echo 配置/提示词/记忆会通过启动.bat 的 HERWORLD_HOME 写回项目目录
explorer "%APP%"
pause
