@echo off
REM What is running, and is the app answering?

echo === services ===
sc query USStockReader | findstr /i "SERVICE_NAME STATE"
sc query Caddy 2>nul | findstr /i "SERVICE_NAME STATE"
sc query postgresql-x64-16 2>nul | findstr /i "SERVICE_NAME STATE"

echo.
echo === app ===
curl.exe -s -o nul -w "  http://127.0.0.1:8000/  ->  HTTP %%{http_code}  (401 = up, asking for the password)\n" http://127.0.0.1:8000/

echo.
echo === last 12 log lines ===
powershell -Command "Get-Content 'C:\apps\us-stock-reader\logs\service.out.log' -Tail 12 -ErrorAction SilentlyContinue"
echo.
pause
