# Deploy — permanent Mini App address (DuckDNS + Caddy)

Gives the aiohttp webapp (admin + user Mini Apps) a **stable HTTPS URL** that
survives restarts, replacing the ephemeral `*.trycloudflare.com` tunnel.

- **Spec:** [`../docs/superpowers/specs/2026-07-31-permanent-webapp-address-design.md`](../docs/superpowers/specs/2026-07-31-permanent-webapp-address-design.md)
- **Plan of record:** DuckDNS (free dynamic DNS) → VPS IP, **Caddy** reverse-proxy
  with automatic Let's Encrypt TLS → `127.0.0.1:8081`.

## Files in this directory

| File | Purpose | Install target |
|------|---------|----------------|
| `Caddyfile` | Reverse proxy + auto TLS for `<sub>.duckdns.org` | `/etc/caddy/Caddyfile` |
| `duckdns/duckdns-update.sh` | Refreshes the DuckDNS A record to the current IP | `/usr/local/bin/duckdns-update.sh` |
| `duckdns/duckdns.service` | systemd oneshot that runs the updater | `/etc/systemd/system/duckdns.service` |
| `duckdns/duckdns.timer` | Fires the updater every 5 min | `/etc/systemd/system/duckdns.timer` |

Everywhere below, replace **`<sub>`** with your chosen DuckDNS subdomain
(so the public URL becomes `https://<sub>.duckdns.org`).

---

## What Claude prepared vs. what the owner must do

**Prepared (in this repo):** `Caddyfile`, DuckDNS updater + systemd unit/timer,
`.env.example` / `docker-compose.yml` guidance, and this checklist.

**Owner-only (needs server access / an account):** register the DuckDNS
subdomain, open ports 80/443, install & run Caddy, run the final cutover restart.

---

## Cutover checklist

### 1. DuckDNS (owner)
1. Sign in at <https://www.duckdns.org> and create a subdomain `<sub>`.
2. Point it at the VPS public IP (or leave it — the updater sets it from the
   request source IP on first run). Copy your account **token**.

### 2. DuckDNS updater on the VPS (owner)
```bash
sudo mkdir -p /etc/duckdns
sudo install -m 600 /dev/null /etc/duckdns/duckdns.env
# Edit it to contain (NO ".duckdns.org" suffix on the domain):
#   DUCKDNS_DOMAINS="<sub>"
#   DUCKDNS_TOKEN="your-duckdns-token"
sudo nano /etc/duckdns/duckdns.env

sudo install -m 755 deploy/duckdns/duckdns-update.sh /usr/local/bin/duckdns-update.sh
sudo cp deploy/duckdns/duckdns.service deploy/duckdns/duckdns.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now duckdns.timer

# Verify one run succeeded (expect "duckdns-update: OK"):
sudo systemctl start duckdns.service
journalctl -u duckdns.service -n 20 --no-pager
dig +short <sub>.duckdns.org        # should print the VPS IP
```

### 3. Firewall / ports (owner)
Open inbound **80** (ACME HTTP-01 challenge) and **443** (TLS):
```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
```
(Also open them in the cloud provider's security group if applicable.)

### 4. Caddy (owner)
```bash
# Install Caddy (Debian/Ubuntu) — see https://caddyserver.com/docs/install
sudo mkdir -p /var/log/caddy
sudo chown -R caddy:caddy /var/log/caddy              # Caddy runs as user `caddy`
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo sed -i 's/<sub>/YOURSUB/' /etc/caddy/Caddyfile   # or edit by hand
sudo caddy validate --config /etc/caddy/Caddyfile     # sanity-check
sudo systemctl enable --now caddy
sudo systemctl restart caddy                          # load the new config
journalctl -u caddy -n 30 --no-pager                  # watch cert issuance
```
Caddy obtains the Let's Encrypt certificate automatically on first request.

### 5. Point the bot at the new URL
Confirm the bot is listening on `127.0.0.1:8081` (docker-compose already
publishes `127.0.0.1:8081:8081`). Then set in `.env`:
```
WEBAPP_URL="https://<sub>.duckdns.org"
```
Restart the bot so new `web_app` buttons embed the stable URL:
```bash
docker compose up -d --force-recreate bot
```

### 6. Retire cloudflared
Once verified, stop the old quick tunnel:
```bash
sudo systemctl stop cloudflared 2>/dev/null || pkill -f 'cloudflared tunnel' || true
```

---

## Verification (spec §Verification)

- [ ] `curl -I https://<sub>.duckdns.org/` → `200` with a valid Let's Encrypt cert.
- [ ] Admin Mini App opens from Telegram (`/admin`).
- [ ] User Mini App / Menu button opens from Telegram.
- [ ] Restart the bot **and** the VPS; the URL still resolves, TLS is valid, and
      the Mini App buttons keep working (address unchanged).
- [ ] `cloudflared` stopped — panel still works without it.

## Rollback

If TLS or DNS misbehaves, revert `WEBAPP_URL` in `.env` to a fresh
`cloudflared tunnel --url http://localhost:8081` URL and restart the bot.
The DuckDNS/Caddy units can stay installed while you debug — they don't affect
the bot unless `WEBAPP_URL` points at them.

---

## Alternative: cloudflared *named* tunnel

Avoids opening inbound ports (tunnel is outbound-only) but needs a Cloudflare
account + a domain on Cloudflare. If you prefer that trade-off, create a named
tunnel, route `<host>` → `http://127.0.0.1:8081`, run `cloudflared` as a service,
and set `WEBAPP_URL=https://<host>`. In that case the DuckDNS + Caddy files here
are unused.
