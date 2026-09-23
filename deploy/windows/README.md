# Running US-Stock Reader on a Windows VPS

Everything here assumes a Windows Server box you can RDP into, with
administrator rights. Budget about half an hour the first time.

**One thing you gain over a Linux server:** TWS and IB Gateway are Windows
applications, so they *can* run here. You do not need them — Unusual Whales
serves the quotes and intraday bars — but if you want IBKR's own feed on the
server, this is the platform where that is straightforward rather than a
project. See the last section.

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
| `IBKR_ENABLED` | **`0`** on a server -- there is no TWS here, and left on it retries and logs forever |
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

---

## Optional: IBKR on the same box

TWS or IB Gateway will run on a Windows VPS, which is the one real advantage
of this platform for this app. Two things to know:

* **It needs a logged-in desktop session.** RDP disconnects leave it running;
  logging off does not. Use `tscon` to disconnect while leaving the session
  alive, or run IB Gateway under a tool that keeps a console session open.
* **The app already works without it.** Every parameter falls back to Unusual
  Whales, and the Settings screen reports IBKR as offline rather than hiding
  it. Get the app running first, add TWS afterwards if you want it.

Defaults in `.env` (`IBKR_HOST=127.0.0.1`, `IBKR_PORT=7497`) are right for
TWS on the same machine. IB Gateway uses 4001 for live, 4002 for paper.
