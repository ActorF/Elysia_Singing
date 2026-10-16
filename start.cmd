@echo off
rem Launch Elysia Singing in CMD with paths anchored to this project.
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1"
if errorlevel 1 (
    echo Runtime preparation did not complete. See the message above.
    pause
    exit /b 1
)
set "ELYSIA_PYTHON=%~dp0models\cache\GPT-SoVITS-v2-240821\runtime\python.exe"
if not exist "%ELYSIA_PYTHON%" (
    echo Runtime is missing. Run setup.cmd first.
    pause
    exit /b 1
)
"%ELYSIA_PYTHON%" -B "%~dp0main.py" %*
set "ELYSIA_EXIT=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %ELYSIA_EXIT%
