# Design: User-Facing Mini App (Queue / Reschedule / Cancel)

**Date:** 2026-07-31
**Status:** Draft — ready for planning (open decisions marked ⚠)
**Scope:** New feature — any user opens a Telegram Mini App showing *their* scheduled posts, and can reschedule or cancel them.
**Scale:** 1–10 users, SQLite, single-process.
**Roadmap:** Item 2 of `docs/superpowers/2026-07-31-roadmap-post-refactor.md`. Plan in a later session.

---

## Problem

Users manage their queue only through bot commands (`/queue`, `/edit`, `/delete`, `/view`). A visual Mini App is friendlier — and the admin Mini App already proves the pattern (aiohttp + `initData` + single-file HTML). The backend DAL for listing/rescheduling/cancelling is **already implemented**; what's missing is a per-user (non-admin) web surface.

## Goals

1. A user opens a Mini App (via a `web_app` button from the bot) and sees their pending scheduled posts.
2. The user can **reschedule** a post (new date/time in their timezone) and **cancel** a post.
3. Authorization is per-user: a user can only ever see/act on **their own** posts.
4. Reuse the admin Mini App's aiohttp + initData + single-file-HTML pattern and the existing user-scoped DAL.

## Non-Goals

- No composing/creating new posts from the Mini App (creation stays in the bot flow) — v1 is view/reschedule/cancel only.
- No editing post *content* in v1 (only time + cancel). ⚠ Content editing is a candidate v2.
- No recurring-pattern management in v1. ⚠ Candidate v2.
- No admin capabilities (this is a separate, non-admin surface).

## Current-State Grounding

- **Auth pattern:** `validate_init_data(init_data, bot_token)` → `{id, ...}` or `None` (`core/webapp.py`). The admin routes wrap it in `_require_admin` (adds `ADMIN_IDS` check). We add a sibling `_require_user` that validates initData and returns the user **without** the admin gate — authorization is then "the post's `user_id` must equal this user's id", enforced by the DAL.
- **DAL already user-scoped and ownership-safe:**
  - `list_pending_posts(user_id, limit, offset)` / `list_editable_pending_posts(user_id, limit, offset)` — the queue.
  - `get_scheduled_post(post_id)` and `get_post_media(post_id)` — detail + media summary.
  - `update_editable_post_time(post_id, user_id, scheduled_at_utc=...)` — reschedule (enforces ownership + editable state).
  - `cancel_post(user_id, post_id)` — cancel (enforces ownership).
- **Timezone:** `store.get_user_timezone(user_id)`; the in-bot picker uses `core/time_picker.py` + `core/utils.parse_local_datetime` / validation — the browser reschedule must produce a UTC epoch consistent with these rules.
- **Serving:** `_STATIC_DIR / "admin.html"` served at `GET /`; `WEBAPP_URL` gating + external TLS (see permanent-address spec).

## Architecture

### Auth (`core/webapp.py`)
- Extract the shared validation into a helper returning the user dict; keep `_require_admin` = shared + `ADMIN_IDS` check; add `_require_user` = shared only (any valid Telegram user).
- Every user route derives `user_id` from `_require_user(request)` and passes it to the DAL — **never** from a request body/param (prevents acting on another user's posts).

### Webapp routes (user-gated)
- `GET /app` (or reuse `/` with a query flag) → serve the new user HTML. ⚠ Decide: separate path/file vs. shared shell.
- `GET /api/my/queue` → `list_editable_pending_posts(user_id, ...)` + per-post kind/preview/local-time; pagination params.
- `POST /api/my/post/{id}/reschedule` → body `{scheduled_at_utc}` (or `{local_datetime, tz}`); validate against `_schedule_validation_text` rules; call `update_editable_post_time`; 404 if not owned/editable.
- `POST /api/my/post/{id}/cancel` → `cancel_post(user_id, id)`; 404 if not owned.
- All return typed JSON; forbidden without valid initData (403).

### Frontend (`core/webapp_static/queue.html` — new)
- List of pending posts: where (destination title), local time, kind (text/media + count), truncated preview — mirror `_build_*_summary` semantics.
- Per-post actions: **Reschedule** (date + time picker respecting the user's timezone) and **Cancel** (with confirm).
- Reuse admin.html's theme-aware CSS + inline-SVG style; render demo data when opened outside Telegram (same trick as admin.html).
- ⚠ Reschedule picker: reimplement a lightweight browser date/time picker vs. send raw local datetime string to the backend and reuse `parse_local_datetime`. **Recommendation:** send `{local_datetime, tz}` and validate/convert server-side to reuse existing, tested time logic (no duplicate validation in JS).

### Bot entry point (`telegram/handlers/`)
- Add a `web_app` button (e.g. extend `/queue` output or a new `/app` command) that opens the user Mini App at `WEBAPP_URL` — mirror how `telegram/admin.py` sends the admin `web_app` button. Only enabled when `WEBAPP_URL` is set.

## Decisions / Open Questions (⚠ for planning)

- ⚠ Which post states are actionable — mirror `list_editable_pending_posts` exactly (only future, non-sending posts).
- ⚠ Reschedule input contract: `{scheduled_at_utc}` computed in JS vs. `{local_datetime, tz}` validated server-side (recommend the latter).
- ⚠ Separate HTML file vs. shared shell with admin.
- ⚠ Include recurring instances / content editing? (Defer to v2.)
- ⚠ Entry point: extend `/queue` vs. new `/app` command.

## Verification

- Webapp tests: `_require_user` accepts any valid initData, rejects bad signature; `GET /api/my/queue` returns only the caller's posts; reschedule/cancel on a post owned by another user → 404 (ownership enforced); invalid reschedule time → 400.
- DAL is already covered; add route-layer ownership tests specifically.
- Manual: open from two different Telegram accounts, confirm each sees only their own queue; reschedule respects timezone; cancel removes the post and the scheduler no longer sends it.
