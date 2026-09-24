# Running US-Stock Reader on a VPS

What you need: a small Linux server (2 vCPU / 4 GB is comfortable; 1 vCPU /
2 GB works), Python 3.12+, Node 20+, Postgres 14+, and a domain name if you
want HTTPS.

**There is nothing to install besides the app.** It used to need a
brokerage desktop application running alongside it for quotes and intraday
bars; that connection was removed. One HTTP feed serves the whole app, so a
server install is the app, Postgres and a reverse proxy.

---

## 1. Server setup

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip nodejs npm \
    postgresql nginx git
sudo -u postgres createuser --pwprompt usstocks
sudo -u postgres createdb -O usstocks usstocks
```

## 2. The application

```bash
sudo mkdir -p /opt/us-stock-reader && sudo chown "$USER" /opt/us-stock-reader
git clone <your-repo-url> /opt/us-stock-reader
cd /opt/us-stock-reader

python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt

cp backend/.env.example backend/.env
# Fill it in. At minimum: ACCESS_PASSWORD, SESSION_SECRET, DATABASE_URL,
# UNUSUAL_WHALES_API_KEY.
chmod 600 backend/.env

cd frontend && npm ci && npm run build && cd ..
```

The backend serves `frontend/dist` itself, so there is nothing else to wire
up between them.

## 3. Run it as a service

```bash
sudo cp deploy/us-stock-reader.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now us-stock-reader
sudo systemctl status us-stock-reader
```

Logs: `journalctl -u us-stock-reader -f`

## 4. Put it behind nginx with HTTPS

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/us-stock-reader
sudo ln -s /etc/nginx/sites-available/us-stock-reader /etc/nginx/sites-enabled/
# edit the file and replace stocks.example.com with your domain
sudo nginx -t && sudo systemctl reload nginx

sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d stocks.example.com
```

The nginx config disables buffering on `/api/analyze/` — the analysis screen
streams its progress, and a buffering proxy holds every event until the run
finishes, which looks exactly like the page having hung.

## 5. Lock the door

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

Port 8000 stays closed to the world; nginx reaches it on localhost.

**Do not skip the password.** Anything reachable on the public internet gets
found. `ACCESS_PASSWORD` is the only thing between a stranger and a screen
full of a provider's data you are licensed to use alone — their terms are one
key, one person, and no redistribution of the data or anything derived from
it. That is a contractual reason on top of the obvious one.

---

## Updating

```bash
cd /opt/us-stock-reader
git pull
backend/.venv/bin/pip install -r backend/requirements.txt
cd frontend && npm ci && npm run build && cd ..
sudo systemctl restart us-stock-reader
```

`deploy/update.sh` does exactly that, if you would rather run one command.

## Checking it works

```bash
curl -I http://127.0.0.1:8000/          # 401 is correct: the login gate
backend/.venv/bin/python -m pytest -q   # from the backend directory
```

A 401 on `/` means the app is up and asking for the password. A connection
refused means it is not running — check `journalctl -u us-stock-reader -n 50`.

## What it costs to run

The app paces its own requests and caches aggressively: a settled trading day
is fetched once and kept for a month, the market-wide scans are one request
each rather than one per symbol, and there is a self-imposed ceiling of 20,000
provider requests a day well under the plan's limit. A normal day of use is a
few hundred. The Settings screen shows the running count.
