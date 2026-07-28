# Admin Mini App + Preview-in-place — Design Spec

**Date:** 2026-07-28
**Status:** Approved (pending implementation)
**Branch:** feature branch off `main`

## Summary

Two independent features for `auto-sender-bot`:

1. **Preview-in-place** — when a user previews a queued post, replace the previous
   preview messages instead of stacking new ones.
2. **Admin panel as a Telegram Mini App** — `/admin` opens a web dashboard (served
   by our own aiohttp server) showing read-only usage statistics as infographics.
   Access restricted to Telegram user IDs listed in `ADMIN_IDS`, authenticated via
   Telegram WebApp `initData`.

No new Python dependencies (aiohttp already present; `hmac`/`hashlib` are stdlib).
No frontend build step (single self-contained HTML page).

---

## Feature 2 — Preview-in-place

### Current behavior
`telegram/router.py:_send_post_preview` (≈line 1316) sends an info message plus the
post content (text or media album) on every `qview:{id}` tap and on the `/view`
path (≈line 2436). Nothing is deleted, so previews accumulate.

### Target behavior
- Track, in the user's FSM state, the message IDs of the current live preview
  (`preview_msg_ids: list[int]`) and `preview_chat_id`.
- On each new preview: delete the previously-tracked messages **first**
  (best-effort — swallow "message not found" / "too old to delete" errors), then
  send the new preview and store its message IDs.
- A media album is multiple messages, so the tracked value is a **list**.
- Result: at most one live preview exists at a time; it "moves down" the chat when
  another post is selected.

### Changes
- `core/notifier.py:send_media_post` returns the list of sent `Message` objects
  (currently returns `None`) so callers can capture message IDs.
- `_send_post_preview` gains a `state: FSMContext` parameter; collects IDs of the
  info message + all content messages; deletes prior IDs before sending.
- `cb_queue_view` (≈line 3978) and the `/view` command path pass `state`.

### Tests
- Deleting old tracked IDs happens before sending the new preview (mock bot;
  assert `delete_message` called with prior IDs, then new IDs stored).

---

## Feature 1 — Admin Mini App

### Config (`core/config.py`, `.env.example`)
- `admin_ids: tuple[int, ...]` parsed from `ADMIN_IDS` (comma-separated, may be empty).
- `webapp_url: str | None` — public HTTPS base URL used for the `web_app` button.
- `webapp_host: str` (default `0.0.0.0`), `webapp_port: int` (default `8081`).
- Mini app is enabled only when `webapp_url` is set.

### Hosting (deploy-time, not code)
The webapp server serves plain HTTP on `WEBAPP_PORT`; TLS is provided externally.
- **Prod:** domain + Caddy (auto Let's Encrypt) reverse-proxying to `WEBAPP_PORT`.
- **Dev:** `cloudflared tunnel --url http://localhost:8081` → temporary HTTPS URL
  set as `WEBAPP_URL`.
Code is identical either way.

### Web server (`core/webapp.py`, new, aiohttp)
- `GET /` → the single-page admin HTML.
- `GET /api/stats` → JSON aggregates.
- `GET /api/user/{id}` → single-user profile card JSON.
- **Auth:** `validate_init_data(init_data: str, bot_token: str, *, max_age_s: int) -> dict | None`
  — a pure function. Validates the Telegram WebApp `initData` HMAC (secret =
  `HMAC_SHA256("WebAppData", bot_token)`), checks `auth_date` freshness, returns the
  parsed `user` dict or `None`. Endpoints read `initData` from the `Authorization`
  header, validate, and require `user.id ∈ admin_ids`, else `403`.
- Started from `main.py` alongside the healthcheck server when `webapp_url` is set.

### Stat aggregates (`core/state.py`, read-only)
- `count_users()`, `avg_destinations_per_user()`
- `count_new_users(since_ts)` (7d / 30d), `count_active_users(since_ts)` (7d / 30d;
  distinct `scheduled_posts.user_id` by `created_at`)
- `count_posts_by_status()` → pending / sent / failed / cancelled
- `count_posts_sent_since(ts)` (today / 7d, by `sent_at`)
- `daily_new_users(days=30)` and `daily_posts_sent(days=30)` — day-bucketed series
  for the line charts
- `language_distribution()`, `count_teams()`, `count_drafts()`
- `top_active_users(limit, since_ts)` → `[(user_id, post_count)]`
- `get_user_profile(user_id)` → channels count, posts count, language, timezone,
  created_at

### Bot (`telegram/admin.py`, new sub-router)
- `/admin`: non-admins are silently ignored. If `webapp_url` is unset, reply that
  the mini app is not configured. Otherwise send a message with an inline `web_app`
  button opening the dashboard.
- Included in the dispatcher in `main.py`.

### Frontend (single self-contained HTML page)
- Vanilla JS + inline SVG charts, no external libraries, no build.
- Uses `Telegram.WebApp`: `expand()`, theme params (light/dark), sends `initData`
  in the `Authorization` header of `fetch`.
- Sections: KPI cards (users, avg channels, active, queue); 30-day line charts
  (new users, sent posts); language donut; posts-by-status bar; top users; find
  user by ID.

### Tests
- DAL aggregates: seed users / destinations / posts, assert counts, average,
  day-buckets, top users, user profile.
- `validate_init_data`: valid hash passes; tampered hash fails; stale `auth_date`
  fails; missing `user` fails.
- Endpoint: `/api/stats` returns 200 with valid admin `initData`, 403 without / for
  non-admin (aiohttp test client).

---

## Implementation phases
- **Phase A** — Feature 2 (preview-in-place). Isolated, fast.
- **Phase B** — Config + stat aggregates + `validate_init_data` + `core/webapp.py`.
- **Phase C** — Frontend dashboard page.
- **Phase D** — `/admin` command (`telegram/admin.py`) + wiring in `main.py`.

## Out of scope (deferred)
- Admin broadcast to all users' DMs (planned for a later version).
