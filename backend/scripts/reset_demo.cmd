@echo off
rem LifeLab 一键重置 demo 演示账号 —— 双击运行即可。
rem 只影响 demo 账号，个人账号数据不受影响。
setlocal
set "SCRIPT_DIR=%~dp0"
set "BACKEND=%SCRIPT_DIR%.."
set "PY=%BACKEND%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -u "%SCRIPT_DIR%reset_demo.py" %*
echo.
echo 重置结束（退出码 %ERRORLEVEL%）。按任意键关闭。
pause >nul
