@echo off
setlocal

set SCRIPT_DIR=%~dp0

where py >nul 2>nul
if errorlevel 1 goto :python_fallback

py -3 --version >nul 2>nul
if errorlevel 1 goto :python_fallback

py -3 "%SCRIPT_DIR%convert_date_to_bids.py" %*
goto :finish

:python_fallback
where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python 3 was not found. Install Python 3 or add it to PATH. 1>&2
    exit /b 9009
)

python "%SCRIPT_DIR%convert_date_to_bids.py" %*

:finish
set EXIT_CODE=%ERRORLEVEL%
endlocal & exit /b %EXIT_CODE%
