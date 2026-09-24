# Running US-Stock Reader on a Windows VPS

Everything here assumes a Windows Server box you can RDP into, with
administrator rights. Budget about half an hour the first time.

---

## 1. Install the prerequisites

From an **administrator** PowerShell:

```powershell
winget install --id Python.Python.3.12 -e
winget install --id OpenJS.NodeJS.LTS -e
winget install --id PostgreSQL.PostgreSQL.16 -e
winget install --id Git.Git -e
winget install --id NSSM.NSSM -e          # runs the app as a Windows service
winget install --id CaddyServer.Caddy -e  # HTTPS with no certificate work
```

Close and reopen PowerShell afterwards so the new commands are on your PATH.

## 2. Create the database

```powershell
& 'C:\Program Files\PostgreSQL\16\bin\createuser.exe' -U postgres --pwprompt usstocks
& 'C:\Program Files\PostgreSQL\16\bin\createdb.exe'   -U postgres -O usstocks usstocks
```

## 3. Get the code and configure it

```powershell
git clone <your-repo-url> C:\apps\us-stock-reader
cd C:\apps\us-stock-reader

python -m venv backend\.venv
backend\.venv\Scripts\pip install -r backend\requirements.txt

Copy-Item backend\.env.example backend\.env
notepad backend\.env
```

Fill in at minimum:

| Setting | What to put |
|---|---|
| `ACCESS_PASSWORD` | the password to open the app |
| `SESSION_SECRET` | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | `postgresql+psycopg://usstocks:yourpassword@localhost:5432/usstocks` |
| `UNUSUAL_WHALES_API_KEY` | your token |
| `ANTHROPIC_API_KEY` | optional — only for the written explanations |

Then build the frontend:

```powershell
cd frontend
npm ci
npm run build
cd ..
```

## 4. Run it as a Windows service

`deploy\windows\install-service.ps1` does this for you. From an
**administrator** PowerShell in the checkout root:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\install-service.ps1
```

It registers `USStockReader` with NSSM, points it at the venv's Python,
sets the working directory, and starts it. After that:

```powershell
Get-Service USStockReader
Restart-Service USStockReader
Get-Content C:\apps\us-stock-reader\logs\service.out.log -Tail 40
```

The service binds to `127.0.0.1:8000` only. Nothing reaches it from outside
until you put Caddy in front, which is the next step and is deliberate.

## 5. HTTPS with Caddy

Point a domain's A record at the VPS first — Caddy gets the certificate
automatically, and it needs the name to resolve.

```powershell
Copy-Item deploy\windows\Caddyfile C:\apps\caddy\Caddyfile
notepad C:\apps\caddy\Caddyfile     # replace stocks.example.com
caddy run --config C:\apps\caddy\Caddyfile
```

Once it works, install Caddy as a service too so it survives a reboot:

```powershell
nssm install Caddy "C:\Program Files\Caddy\caddy.exe" "run --config C:\apps\caddy\Caddyfile"
nssm start Caddy
```

## 6. Firewall

Open 80 and 443. **Leave 8000 closed** — Caddy reaches it over localhost.

```powershell
New-NetFirewallRule -DisplayName "HTTP"  -Direction Inbound -LocalPort 80  -Protocol TCP -Action Allow
New-NetFirewallRule -DisplayName "HTTPS" -Direction Inbound -LocalPort 443 -Protocol TCP -Action Allow
```

And lock down RDP while you are here: restrict port 3389 to your own IP
rather than leaving it open to the internet.

> **Do not run this without a password.** Anything on the public internet
> gets found within hours. `ACCESS_PASSWORD` is the only thing between a
> stranger and a screen full of data you are licensed to use alone — Unusual
> Whales' terms are one key, one person, and no redistribution of the data or
> anything derived from it. That is a contractual reason on top of the
> obvious one.

## Day to day: the batch files

Double-click these in `deploy\windows\` -- each asks for administrator
rights itself, so there is no "run as administrator" to remember:

| File | What it does |
|---|---|
| `start-server.bat` | Starts the app (and Caddy), then checks it answers |
| `stop-server.bat` | Stops both |
| `restart-server.bat` | Restart after editing `.env` or pulling changes |
| `status.bat` | What is running, whether the app answers, and the last log lines |

A `HTTP 401` from those scripts is the **correct** answer: the app is up and
asking for the password. "Connection refused" means it is not running.

## Updating

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\update.ps1
```

Pulls, reinstalls dependencies, rebuilds the frontend, runs the tests, and
restarts the service — stopping if the tests fail.

## Checking it works

```powershell
curl.exe -I http://127.0.0.1:8000/
```

**401 is the correct answer** — the app is up and asking for the password.
"Connection refused" means the service is not running; check the log at
`logs\service.err.log`.

