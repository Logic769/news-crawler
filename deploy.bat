@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1"
if errorlevel 1 (
    echo.
    echo Deployment failed. Please check the error above.
    exit /b 1
)
endlocal
