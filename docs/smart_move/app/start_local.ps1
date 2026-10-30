# Starts the read-only Smart Move API (8791) and the page (5191). Run from anywhere; stops nothing else.
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
Start-Process -FilePath "python" -ArgumentList "-m","uvicorn","smart_move_local_server:app","--host","127.0.0.1","--port","8791" -WorkingDirectory "$root\backend" -WindowStyle Hidden
Start-Process -FilePath "npx.cmd" -ArgumentList "vite","--config","vite.smart-move.config.ts" -WorkingDirectory "$root\frontend" -WindowStyle Hidden
Write-Host "Smart Move: http://127.0.0.1:5191/smart-move  (API docs http://127.0.0.1:8791/docs)"
