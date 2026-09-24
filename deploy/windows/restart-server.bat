@echo off
REM Restart US-Stock Reader. Use this after editing .env or pulling changes.

net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo Restarting US-Stock Reader...
net stop USStockReader 2>nul
net start USStockReader

echo.
echo Waiting for it to come up...
timeout /t 6 /nobreak >nul
curl.exe -s -o nul -w "  HTTP %%{http_code} (401 = up, asking for the password)\n" http://127.0.0.1:8000/
echo.
echo Recent log:
powershell -Command "Get-Content 'C:\apps\us-stock-reader\logs\service.out.log' -Tail 8 -ErrorAction SilentlyContinue"
echo.
pause
