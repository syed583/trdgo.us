# Cloudflare + Caddy setup for tradgo.us

Goal: put a Cloudflare edge in front of the app so the slow parts measured from
Dubai — DNS (~184 ms), the TLS handshake (~180–330 ms) and the ~1 MB of static
JS/CSS — happen at an edge **near the user** instead of crossing to the Europe
VPS on every request. The VPS itself answers in ~50 ms; the latency is the
network path, and that is exactly what an edge network fixes.

## Non‑negotiable: live market data is NEVER edge‑cached

This app is real‑time. Only the **content‑hashed static build** (`/assets/*`,
which can never change contents for a given URL) is cached. Everything dynamic —
`/api/*`, `/auth/*`, and the SPA `index.html` — is **bypassed**. The origin
already sends the right headers (`immutable` on `/assets`, `no-cache` on the
HTML, nothing cacheable on `/api`), and the Cloudflare rules below add an
explicit bypass on top, so prices, options flow, earnings, and signals are
always fetched live.

---

## Prerequisites

- The VPS public IP — on the VPS run: `powershell -c "(Invoke-RestMethod ifconfig.me/ip)"`
- GoDaddy account login (the domain registrar).
- Cloudflare account (free plan is enough) — https://dash.cloudflare.com/sign-up

---

## Step 1 — Add the site to Cloudflare

1. Cloudflare dashboard → **Add a site** → enter `tradgo.us` → choose **Free**.
2. Cloudflare scans existing DNS. On the DNS screen, make sure there is an
   **A** record:
   - Type `A`, Name `tradgo.us` (shows as `@`), Content `<VPS_PUBLIC_IP>`,
     Proxy status **Proxied** (orange cloud). 
   - If you use `www`, add `A` `www` → same IP, **Proxied** too (or a CNAME
     `www` → `tradgo.us`, proxied).
3. Cloudflare shows **two nameservers**, e.g. `xxx.ns.cloudflare.com` and
   `yyy.ns.cloudflare.com`. Copy both — you need them in Step 2.

## Step 2 — Point GoDaddy at Cloudflare's nameservers

1. GoDaddy → **My Products** → next to `tradgo.us` click **DNS** (or **Manage
   DNS**).
2. Scroll to **Nameservers** → **Change** → **Enter my own nameservers
   (advanced)**.
3. Remove GoDaddy's nameservers and enter Cloudflare's two.
4. Save. Propagation is usually minutes, up to a few hours. Cloudflare emails
   you when the site is **Active**. (Until then the site keeps working on the
   old path — no downtime.)

> From now on DNS is managed in **Cloudflare**, not GoDaddy. Any future DNS
> change you make in GoDaddy will be ignored.

## Step 3 — SSL/TLS: Full (strict) with an Origin Certificate

Caddy currently gets its public cert from Let's Encrypt over port 80. Behind
Cloudflare that HTTP‑01 challenge can fail, so we give Caddy a **Cloudflare
Origin Certificate** instead (valid only between Cloudflare and your origin).

1. Cloudflare → **SSL/TLS** → **Overview** → set encryption mode to
   **Full (strict)**.
2. Cloudflare → **SSL/TLS** → **Origin Server** → **Create Certificate**
   → leave defaults (RSA, hostnames `tradgo.us, *.tradgo.us`, 15 years) →
   **Create**.
3. You get two blocks of text. On the VPS, save them:
   - the **Origin Certificate** → `C:\apps\certs\tradgo-origin.pem`
   - the **Private Key** → `C:\apps\certs\tradgo-origin.key`
   (create `C:\apps\certs` first; keep the key readable only by the service
   account.)

## Step 4 — Caddyfile on the VPS

The app serves the frontend **and** the API from uvicorn on `127.0.0.1:8000`
(the `/assets/*` immutable caching and the `no-cache` HTML are set by the app
itself, so Caddy just needs to proxy and present the origin cert). Replace the
VPS Caddyfile with:

```
tradgo.us, www.tradgo.us {
	# Origin cert for Cloudflare "Full (strict)".
	tls C:\apps\certs\tradgo-origin.pem C:\apps\certs\tradgo-origin.key

	encode zstd gzip

	# Only Cloudflare should reach the origin directly; everything goes to the app.
	reverse_proxy 127.0.0.1:8000 {
		header_up X-Forwarded-Proto {scheme}
		header_up X-Forwarded-For {remote_host}
	}
}
```

Then reload Caddy (as admin):

```
caddy reload --config C:\path\to\Caddyfile
```

(or restart the Caddy service, however it is installed). Test locally on the
VPS: `curl.exe -I https://tradgo.us/health --resolve tradgo.us:443:127.0.0.1`
should return `200`.

> Optional hardening (do after everything works): restrict the origin firewall
> so port 443 only accepts Cloudflare's published IP ranges
> (https://www.cloudflare.com/ips/), so nobody can bypass the edge.

## Step 5 — Cache rules (protect live data)

Cloudflare → **Caching** → **Cache Rules** → create these, in order:

1. **Bypass dynamic + auth** (most important):
   - Rule name: `bypass-api-auth`
   - When: `URI Path starts with "/api"` **OR** `URI Path starts with "/auth"`
   - Then: **Bypass cache**.

2. **Cache the hashed build long** (safe — filenames change on every deploy):
   - Rule name: `cache-assets`
   - When: `URI Path starts with "/assets/"`
   - Then: **Eligible for cache**, **Edge TTL: respect origin** (the origin
     already says `max-age=31536000, immutable`), **Browser TTL: respect
     origin**.

3. Leave the SPA `index.html` alone — the origin sends `no-cache`, and with no
   matching cache rule Cloudflare will not store it, so a new deploy is picked
   up immediately.

> Do **not** create a "Cache Everything" page rule for `tradgo.us/*` — that is
> the one setting that would cache live market JSON. The rules above cache only
> `/assets/*`.

## Step 6 — Speed toggles (all safe for live data)

- **Speed → Optimization → Content Optimization**: Brotli **On**.
- **Network**: HTTP/3 (QUIC) **On**, 0‑RTT **On**, WebSockets **On**.
- **Caching → Tiered Cache**: **On** (smart tiered — shortens the edge→origin path).
- **Speed → Optimization → Argo Smart Routing** *(paid, ~$5/mo + usage)*:
  optional, but it directly improves the Dubai→Europe origin path that your
  measurements showed is slow and jumpy. Worth trying for a real‑time app.

## Step 7 — Verify (from your Dubai machine, after Cloudflare is Active)

```
curl.exe -s -o NUL -w "dns:%{time_namelookup}s connect:%{time_connect}s tls:%{time_appconnect}s ttfb:%{time_starttransfer}s total:%{time_total}s\n" https://tradgo.us/health
```

Expect **DNS** and **TLS** to drop a lot (served from a near edge). Confirm a
static asset is edge‑cached and a live call is not:

```
curl.exe -sI https://tradgo.us/assets/<some-file>.js | findstr /i "cf-cache-status"   # expect HIT (after a warm-up)
curl.exe -sI https://tradgo.us/api/health          | findstr /i "cf-cache-status"   # expect DYNAMIC/BYPASS
```

`cf-cache-status: DYNAMIC` or `BYPASS` on `/api` is the proof your live data is
**not** cached.

## Rollback

If anything misbehaves: Cloudflare → DNS → set the record's proxy to **DNS
only** (grey cloud) to send traffic straight to the origin again, or at GoDaddy
restore the original nameservers. Either way the app keeps serving; you only
lose the edge acceleration.
