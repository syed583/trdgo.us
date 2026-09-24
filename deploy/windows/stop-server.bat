@echo off
REM Stop US-Stock Reader (and Caddy, if it is installed).

net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

sc query Caddy >nul 2>&1
if %errorlevel% equ 0 (
    echo Stopping Caddy...
    net stop Caddy 2>nul
)

echo Stopping US-Stock Reader...
net stop USStockReader 2>nul
if %errorlevel% equ 2 echo   was not running.

echo.
sc query USStockReader | findstr /i "STATE"
echo.
pause
