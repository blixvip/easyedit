@echo off
rem Windows launcher for easyedit.
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" -m easyedit %*
  exit /b %ERRORLEVEL%
)
where python3.12 >nul 2>&1 && (
  python3.12 -m easyedit %*
  exit /b %ERRORLEVEL%
)
py -3.12 -m easyedit %*
