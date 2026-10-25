@echo off
chcp 65001 >nul
echo ============================================
echo  设置 API Key
echo ============================================
echo.
echo  【推荐】直接在程序里填：
echo     回到外层双击「启动.bat」→ 右下角齿轮 / 设置 → 上面的「连接」页
echo     选服务商、粘 key、点「保存到本机」。
echo     那里存的是加密过的（Windows DPAPI），只绑当前账户。
echo.
echo  【另一个办法】设成环境变量（适合多套程序共用一个 key）：
echo     粘到下面回车，会写进当前用户的环境变量，
echo     不落在项目里，也不会被备份/打包带走。
echo     环境变量的优先级**高于**程序里存的 key。
echo.
echo  【提醒】旧版把 key 明文写进过 config.json，
echo     那把 key 应该去平台轮换掉，别继续用。
echo.
set /p KEY=要写进环境变量的 key（直接回车 = 什么也不做）: 
if "%KEY%"=="" (
  echo 没输入，什么也没做。
  pause
  exit /b
)
setx DEEPSEEK_API_KEY "%KEY%"
echo.
echo 已写入 DEEPSEEK_API_KEY。setx 只对**新开的**程序生效，
echo 请关掉这个窗口，回到外层重新双击「启动.bat」。
echo （接别家的话，环境变量名不一样：OPENAI_API_KEY / DASHSCOPE_API_KEY ...
echo   具体看设置「连接」页上写的那一行，或者干脆在界面里填。）
pause
