@echo off
REM Start US-Stock Reader (and Caddy, if it is installed).
REM
REM Service control needs administrator rights, so this re-launches itself
REM elevated rather than failing with "Access is denied" halfway through.

net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo Starting US-Stock Reader...
net start USStockReader 2>nul
if %errorlevel% equ 2 echo   already running.

sc query Caddy >nul 2>&1
if %errorlevel% equ 0 (
    echo Starting Caddy...
    net start Caddy 2>nul
    if %errorlevel% equ 2 echo   already running.
)

echo.
sc query USStockReader | findstr /i "STATE"
echo.
echo Checking the app answers...
curl.exe -s -o nul -w "  HTTP %%{http_code} (401 = up, asking for the password)\n" http://127.0.0.1:8000/
echo.
pause
