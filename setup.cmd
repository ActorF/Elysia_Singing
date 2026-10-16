@echo off
rem Copy the trusted local Elysia AI Windows audio runtime into this project.
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 goto use_py
where python >nul 2>nul
if not errorlevel 1 goto use_python
echo Python 3.10 or newer is required to run setup.
exit /b 1

:use_py
py -3 "%~dp0setup_runtime.py" %*
exit /b %ERRORLEVEL%

:use_python
python "%~dp0setup_runtime.py" %*
exit /b %ERRORLEVEL%
