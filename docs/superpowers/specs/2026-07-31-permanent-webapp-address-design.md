# Design: Permanent Mini App Address (Ops)

**Date:** 2026-07-31 · **Artifacts prepared:** 2026-08-01
**Status:** Claude-side artifacts DONE (branch `feature/permanent-webapp-address`) — owner cutover pending. Ops/hosting task.
**Artifacts:** `deploy/Caddyfile`, `deploy/duckdns/{duckdns-update.sh,duckdns.service,duckdns.timer}`, `deploy/README.md` (cutover runbook + verification), plus `.env.example` / `docker-compose.yml` guidance. Owner-only steps (DNS registration, ports 80/443, install Caddy, final restart) tracked in `deploy/README.md`.
**Scope:** Infrastructure — give the webapp a stable public HTTPS address so the Mini App(s) don't break on restart.
**Scale:** 1–10 users; single VPS.
**Roadmap:** Item 5 (last) of `docs/superpowers/2026-07-31-roadmap-post-refactor.md`.

---

## Problem

`WEBAPP_URL` is currently a temporary `*.trycloudflare.com` tunnel that changes on every tunnel/server restart. Telegram Mini App buttons embed `WEBAPP_URL`, so each restart breaks the admin panel (and, once shipped, the user Mini App). A stable address is needed before relying on the web surfaces.

## Goals

1. A stable HTTPS URL for the webapp that survives restarts.
2. Automatic TLS (no manual cert management).
3. The bot's `web_app` buttons keep working across restarts.

## Non-Goals

- No change to `core/webapp.py` app logic — it keeps serving plain HTTP on `WEBAPP_HOST:WEBAPP_PORT` (default `0.0.0.0:8081`); TLS stays external (unchanged from the admin Mini App design).
- No move off the single-VPS model.

## Current-State Grounding

- Server: aiohttp on `127.0.0.1:8081` (plain HTTP), wired in `main.py` next to the healthcheck server; enabled only when `WEBAPP_URL` is set (`core/config.py`).
- Today: `cloudflared` quick tunnel provides the ephemeral HTTPS front.

## Plan of Record: DuckDNS + Caddy

1. **DuckDNS:** register a free subdomain `<sub>.duckdns.org` pointing at the VPS public IP (`72.56.118.208` per Open Questions). Add a DuckDNS updater (cron or a small systemd timer) so the record follows the IP if it changes.
2. **Caddy on the VPS:** reverse-proxy `https://<sub>.duckdns.org` → `127.0.0.1:8081`, with automatic Let's Encrypt TLS. Minimal `Caddyfile`:
   ```
   <sub>.duckdns.org {
       reverse_proxy 127.0.0.1:8081
   }
   ```
3. **Firewall/ports:** open **80** and **443** (Caddy needs 80 for the ACME HTTP-01 challenge and 443 for TLS).
4. **Cutover:** set `WEBAPP_URL=https://<sub>.duckdns.org` in `.env`, restart the bot, stop `cloudflared`.
5. **Verify:** open the Mini App from Telegram; restart the server and confirm the URL still resolves and TLS is valid.

**Alternative:** a cloudflared **named** tunnel (stable hostname) — needs a Cloudflare account + a domain on Cloudflare. Avoids opening inbound ports (tunnel is outbound-only). Trade-off: Cloudflare dependency vs. DuckDNS+Caddy's open-ports requirement.

## What Claude can/can't do

- **Can prepare:** the `Caddyfile`, a DuckDNS updater script + systemd unit/timer, `.env` / `docker-compose.yml` edits, and a step-by-step cutover checklist.
- **Owner must do:** DNS registration, opening ports 80/443, installing/running Caddy on the VPS, and the final restart — these need server access and are outside Claude's reach. Confirm before any change that publishes or alters hosting.

## Verification

- `curl -I https://<sub>.duckdns.org/` returns 200 and a valid Let's Encrypt cert.
- Mini App opens from Telegram after a full server restart (address unchanged).
- `cloudflared` no longer required; removing it doesn't break the panel.
