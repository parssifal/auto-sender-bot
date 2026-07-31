# Roadmap — Post-Refactor Feature Work

**Date:** 2026-07-31
**Status:** Planning — sequence agreed with owner; each item still needs its own brainstorm → spec → plan before implementation.
**Context:** All 4 modular-refactoring phases are done (`docs/superpowers/specs/2026-04-12-modular-refactoring-design.md`). The codebase is clean (no TODO/FIXME markers, 264 tests green). This roadmap sequences the next batch of feature + tech-debt work.

**Agreed order:** 1) Broadcast → 2) User-facing Mini App → 3) Dashboard auto-refresh → 4) Tech debt → **(last)** permanent Mini App address.

---

## Shared foundation: write-capable webapp

Two items below (Broadcast admin action, User-facing Mini App) are the **first write operations** through the aiohttp webapp (`core/webapp.py` today exposes only `GET /`, `/api/stats`, `/api/user/{id}`, `/api/users`, all admin-gated via Telegram `initData`). Both need:

- **POST endpoints** with body validation.
- **Auth reuse:** the existing `validate_init_data()` (HMAC via aiogram `check_webapp_signature` + `auth_date` freshness). Admin actions additionally check `ADMIN_IDS`; user actions authorize **the requesting user against their own data** (no `ADMIN_IDS` gate).
- **Idempotency / double-submit protection** on destructive actions.

**Sequencing note:** doing **Broadcast first** establishes the admin POST + auth pattern; the **User Mini App** reuses that plumbing with a per-user (non-admin) auth variant. This is why the agreed order is efficient.

---

## 1. Broadcast to all users' DMs (from admin panel)

**Goal:** An admin composes a message once and the bot delivers it to every registered user's private chat, with a delivery report.

**Why now:** Long-deferred (`2026-07-28-admin-mini-app-and-preview-design.md` §Out of scope). The infra it needs (admin panel, initData auth, `notifier.send_text`/`send_media_post`) now all exists.

**Already exists (grounding):**
- `notifier.send_text(bot, chat_id, ...)` and `notifier.send_media_post(...)` — send to any `chat_id`; for a DM, `chat_id == user.id`.
- `users` table: `id, language, username, first_name, created_at` — the recipient set + per-user language for optional i18n.
- `StateStore.list_users(limit, offset)` — pageable user enumeration (add a lightweight `all_user_ids()` or reuse with paging).
- Admin auth + `ADMIN_IDS` gating pattern in `webapp.py`.

**New work:**
- DAL: `all_active_user_ids()` (or reuse `list_users` paging); optional `broadcast_log` table to record a run + per-user delivery status (delivered / blocked / failed) if we want auditable history.
- Service: `core/services/admin_broadcast_svc.py` — iterate recipients, send via `notifier`, **catch `TelegramForbiddenError`** (user blocked the bot) and other send errors per-recipient, throttle to respect Telegram rate limits (~30 msg/s global; add a small `asyncio.sleep` / semaphore), aggregate a `{delivered, blocked, failed}` summary.
- Webapp: `POST /api/broadcast` (admin-gated) accepting message text/entities (and optionally media); returns the delivery summary. Consider running the send in a background task with a status endpoint if the user base grows (at 1–10 users a synchronous send is fine — YAGNI the background job until needed).
- Frontend: a compose form + "send to all" button + result panel in `admin.html`. **First destructive admin action** → add a confirm step.

**Decisions to make in brainstorm:** text-only v1 vs media support; synchronous vs background send; whether to persist a broadcast history; whether to also expose it as a `/admin_broadcast` bot command (alternative to the panel).

**Size:** Small–Medium. **Risks:** rate limits; partial-failure reporting; it's a write endpoint (auth + confirm discipline).

---

## 2. User-facing Mini App (view queue / reschedule / cancel)

**Goal:** Any user opens a Mini App showing *their* scheduled posts and can reschedule or cancel them — a friendlier surface than the `/queue` `/edit` `/delete` bot commands.

**Why now:** Noted in project memory as "second spec planned, not yet started." Highest user-facing value of this batch.

**Already exists (grounding) — most of the backend is done:**
- DAL, user-scoped and permission-safe: `list_pending_posts(user_id, ...)`, `list_editable_pending_posts(user_id, ...)`, `get_scheduled_post(post_id)`, `update_editable_post_time(post_id, user_id, scheduled_at_utc=...)`, `cancel_post(user_id, post_id)`, `get_post_media(post_id)`. These already enforce `user_id` ownership.
- Reference pattern: the admin Mini App (`admin.html` + aiohttp + initData) is the exact template — theme-aware single-file HTML, `WEBAPP_URL` gating, TLS-external hosting.

**New work:**
- Auth variant: `initData`-validated but authorizing **the requesting user against their own posts** (no `ADMIN_IDS` restriction). Extract the shared validation so admin and user routes reuse it with different authorization.
- Webapp routes (user-gated): `GET /api/my/queue` (that user's pending posts + media summaries), `POST /api/my/post/{id}/reschedule` (new `scheduled_at_utc`; reuse `update_editable_post_time`), `POST /api/my/post/{id}/cancel` (reuse `cancel_post`). Return typed JSON.
- Frontend: a new user-facing HTML (`core/webapp_static/queue.html` or similar) — list posts, per-post reschedule (date/time picker) + cancel with confirm. Reuse admin.html's theme + inline-SVG style.
- Entry point: a `web_app` button from a bot command (e.g. extend `/queue` or a new `/app`) that opens the user Mini App, mirroring how `/admin` opens the admin one.
- Timezone: reschedule UI must respect the user's stored timezone (`get_user_timezone`) exactly like the in-bot time picker.

**Decisions to make in brainstorm:** which post states are reschedulable/cancelable (mirror `list_editable_pending_posts` rules); reuse vs. reimplement the calendar/time picker in the browser; whether to also surface recurring patterns; pagination.

**Size:** Medium (backend mostly done; frontend + auth-refactor is the bulk). **Risks:** per-user auth correctness (must never let user A touch user B's posts — DAL already guards, but verify at the route layer too); timezone correctness on reschedule.

---

## 3. Admin dashboard auto-refresh

**Goal:** The admin panel refreshes its stats without the manual "↻" button.

**Already exists:** `GET /api/stats` returns the full aggregate bundle; the ↻ button already re-fetches it.

**New work (frontend-only, smallest item):**
- Client-side polling in `admin.html`: `setInterval` re-fetching `/api/stats` (and the active detail view) every N seconds, with a visible "last updated" timestamp and a pause-on-hidden-tab guard (`document.visibilityState`). Keep the manual ↻ as an immediate refresh.
- No backend change needed. (SSE/websocket is overkill at this scale — YAGNI.)

**Decisions:** poll interval (e.g. 30–60s); whether to also auto-refresh the open user-detail card.

**Size:** Tiny. **Risks:** minimal (avoid hammering the endpoint; pause when tab hidden).

---

## 4. Tech debt

Optional hardening, ordered by value. None blocks features.

- **4a. `core/state.py` split (~2160 lines) — optional "Phase 5".** The last remaining large module. Phase 3 decision **A1** deliberately kept stats aggregates + transactional methods in the DAL, so its size is intentional, not accidental. If we split, do it by domain (users / destinations / posts / recurring / drafts / teams / stats) behind the same `StateStore` facade so callers/tests are unaffected — mirror the Phase 1–3 "extract under tests, one domain at a time" discipline. **Recommendation:** only do this if `state.py` starts causing real navigation/merge pain; otherwise leave it (YAGNI).
- **4b. Symmetric typed FSM writes (`patch_*_ctx`).** Phase 4 typed only reads; writes stay flat `update_data(**keys)`. Add typed write wrappers only if a key-name-typo bug actually appears on the write side (none observed). Low priority.
- **4c. Finish typing the datetime-picker nav handlers in `shared.py`.** Deliberately left on defensive raw `data.get(...)` in Phase 4 (they forward the raw `data` dict to prompt helpers). Consistency-only; low value.
- **4d. Retire / archive `TODO.md` (109 KB).** It's the historical v2.0 build plan (time picker / recurring / drafts-teams) — all shipped. It's stale as a live backlog and dwarfs the real docs. **Recommendation:** move it to `docs/archive/` (or delete) and point contributors at `docs/superpowers/` + this roadmap.
- **4e. Push `main` to origin.** Local `main` is ahead of `origin/main` by 48 commits (Phases 2–4 not on GitHub). Not tech debt per se, but the work isn't backed up remotely. **Do this early**, independent of everything else.

---

## Last: permanent Mini App address (after 1–4)

**Goal:** Replace the ephemeral `*.trycloudflare.com` `WEBAPP_URL` (changes on every tunnel/server restart) with a stable HTTPS address so the Mini App(s) don't break.

**Plan of record (from Open Questions):** DuckDNS free subdomain (`*.duckdns.org` → server IP) + **Caddy** on the VPS (automatic Let's Encrypt TLS) reverse-proxying to `127.0.0.1:8081`. Requires open ports 80/443. Then set `WEBAPP_URL=https://<sub>.duckdns.org` and stop cloudflared. **Alternative:** cloudflared *named* tunnel (needs a Cloudflare account + domain).

**Why last:** it's an ops/hosting task requiring server access and DNS/ports — mostly on the owner's side. Claude can prepare the Caddyfile, a DuckDNS updater unit, and `.env`/compose changes, but the cutover needs the owner. Doing it after the features means it stabilizes hosting for *both* Mini Apps at once.

---

## Suggested execution flow per item

For each of 1–4, follow the project's established discipline:
1. **Brainstorm** (`superpowers:brainstorming`) — lock requirements + open decisions listed above.
2. **Spec** in `docs/superpowers/specs/YYYY-MM-DD-<feature>.md`.
3. **Plan** in `docs/superpowers/plans/YYYY-MM-DD-<feature>.md` (TDD, bite-sized, guard-rail tests).
4. **Execute** under tests, one branch per feature (`feature/<name>`), merge to `main` when green.

**Immediate low-cost wins to do independent of the above:** 4e (push `main`) and 4d (archive `TODO.md`).
