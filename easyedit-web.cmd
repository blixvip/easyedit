@echo off
rem Local web UI for easyedit (http://127.0.0.1:4331)
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" -m easyedit.web %*
  exit /b %ERRORLEVEL%
)
where python3.12 >nul 2>&1 && (
  python3.12 -m easyedit.web %*
  exit /b %ERRORLEVEL%
)
py -3.12 -m easyedit.web %*
