@echo off
rem LifeLab PC 采集器入口 —— 双击运行即可。
rem 也可带参数，例如：pc_collector.cmd --setup  /  pc_collector.cmd --once --duration 30
setlocal
set "SCRIPT_DIR=%~dp0"
set "BACKEND=%SCRIPT_DIR%.."
set "PY=%BACKEND%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -u "%SCRIPT_DIR%pc_collector.py" %*
echo.
echo 采集器已退出（退出码 %ERRORLEVEL%）。按任意键关闭。
pause >nul
