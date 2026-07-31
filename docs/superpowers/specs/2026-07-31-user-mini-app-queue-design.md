# Design: User-Facing Mini App (Queue / Reschedule / Cancel + Recurring)

**Date:** 2026-07-31
**Status:** Implemented (branch `feature/user-mini-app-queue`, 2026-07-31; plan `docs/superpowers/plans/2026-07-31-user-mini-app-queue.md`)
**Scope:** New feature — any user opens a Telegram Mini App showing *their* scheduled posts and recurring patterns, and can reschedule/cancel one-off posts and cancel recurring series.
**Scale:** 1–10 users, SQLite, single-process.
**Roadmap:** Item 2 of `docs/superpowers/2026-07-31-roadmap-post-refactor.md`.

---

## Problem

Users manage their queue only through bot commands (`/queue`, `/edit`, `/delete`, `/view`, `/recurring`). A visual Mini App is friendlier — and the admin Mini App already proves the pattern (aiohttp + `initData` + single-file HTML). The backend DAL for listing/rescheduling/cancelling one-off posts **and** listing/cancelling recurring patterns is **already implemented**; what's missing is a per-user (non-admin) web surface.

## Goals

1. A user opens a Mini App (via a `web_app` button from the bot) and sees their pending one-off posts and their active recurring patterns.
2. The user can **reschedule** a one-off post (new date/time in their timezone) and **cancel** a one-off post.
3. The user can **cancel** a recurring pattern (deactivates the series and cancels its pending instances).
4. Authorization is per-user: a user can only ever see/act on **their own** posts and patterns.
5. Reuse the admin Mini App's aiohttp + initData + single-file-HTML pattern and the existing user-scoped DAL.

## Non-Goals

- No composing/creating new posts from the Mini App (creation stays in the bot flow) — v1 is view/reschedule/cancel only.
- No editing one-off post *content* in v1 (only time + cancel). Content editing is a candidate v2.
- No editing a recurring pattern's cadence/schedule in v1 — there is no DAL primitive for it (in-bot has none either), and it is a meaningfully larger backend addition. Recurring v1 = **list + cancel series**. Editing cadence is a candidate v2.
- No admin capabilities (this is a separate, non-admin surface).

## Locked Decisions

- **Entry points:** BOTH — a new `/app` command AND a `web_app` button appended to `/queue` output. The existing `/queue` text list stays as a fallback. Both only enabled when `WEBAPP_URL` is set.
- **v1 scope:** view + reschedule + cancel one-off posts; view + cancel recurring patterns.
- **Reschedule input contract:** browser sends `{local_datetime: "DD.MM.YYYY HH:MM"}`; the timezone is authoritative **server-side** from `get_user_timezone(user_id) or "UTC"` (browser does NOT send tz). The `or "UTC"` fallback is mandatory — `get_user_timezone` returns `str | None` and the codebase convention is `... or "UTC"` (shared.py, helpers.py, drafts.py); passing `None` into `parse_local_datetime` → `ZoneInfo(None)` would 500. Server reuses `parse_local_datetime` + `validate_schedule_time` — no duplicated validation in JS.
- **Post states actionable:** mirror `list_editable_pending_posts` exactly (only future, non-sending pending posts).
- **HTML:** a new, separate `core/webapp_static/queue.html` (not a shared shell with admin).

## Current-State Grounding

- **Auth pattern:** `validate_init_data(init_data, bot_token)` → user dict or `None` (`core/webapp.py`). Admin routes wrap it in `_require_admin` (adds `ADMIN_IDS` check). We add a sibling `_require_user` that validates initData and returns the user **without** the admin gate — authorization is then "the row's `user_id` must equal this user's id", enforced by the DAL. initData arrives in the `Authorization: tma <initData>` header (`_extract_init_data`).
- **DAL already user-scoped and ownership-safe:**
  - One-off: `list_editable_pending_posts(user_id, limit, offset)`, `get_scheduled_post(post_id)`, `get_post_media(post_id)`, `update_editable_post_time(post_id, user_id, scheduled_at_utc=...)`, `cancel_post(user_id, post_id)`.
  - Recurring: `list_user_recurring_summaries(user_id, offset, limit, include_inactive)` → `RecurringPatternSummary` (pattern + destination title/username + next pending instance's `next_post_id`/`next_scheduled_at_utc`/`next_post_status`); `cancel_recurring_pattern(user_id, pattern_id)` → transactional: sets `is_active=0` and cancels the pattern's still-pending instances. Both are user-scoped.
- **Timezone / validation:** `store.get_user_timezone(user_id)`; `core/utils.parse_local_datetime(text, tz_name)` (expects `"DD.MM.YYYY HH:MM"`) → `ParsedScheduleTime.utc_epoch`; `core/utils.validate_schedule_time(utc_timestamp, now_utc=...)` → `ScheduleTimeValidation(is_valid, error_key)` (rejects past / insufficient lead).
- **Serving:** `_STATIC_DIR / "admin.html"` served at `GET /`; `WEBAPP_URL` gating + external TLS.

## Architecture

### Auth (`core/webapp.py`)
- Extract the shared validation (initData → user dict) so both gates reuse it.
- `_require_admin` = shared + `ADMIN_IDS` check (unchanged behavior).
- `_require_user` = shared only (any valid Telegram user); returns the user dict or `None`.
- Every user route derives `user_id` from `_require_user(request)` and passes it to the DAL — **never** from a request body/param.

### Webapp routes (user-gated)
All return typed JSON; forbidden without valid initData (403).

- `GET /app` → serve `queue.html`.
- `GET /api/my/queue` → `list_editable_pending_posts(user_id, ...)` mapped to per-post `{id, destination_title, scheduled_at_utc, local_time, kind, media_count, preview}`. Pagination params (`limit`/`offset`). **Note:** `list_editable_pending_posts` returns `ScheduledPostRow` (`SELECT sp.*`) which carries only `chat_id` — no destination title. The route resolves the title per post via `get_destination_title(chat_id)` (state.py:1593). `media_count`/`preview` derive from `get_post_media(post_id)` + the row's text/caption.
- `GET /api/my/recurring` → `list_user_recurring_summaries(user_id, ...)` mapped to `{id, destination_title, interval_description, next_scheduled_at_utc, next_local_time}`. **Note:** `interval_description` is NOT a field on `RecurringPatternSummary` (it exposes nested `pattern.interval_type` / `weekdays_mask` / `time_of_day_minutes`); the route must build a human string. Reuse `telegram/handlers/keyboards.py::_repeat_interval_label(lang, interval_type)` as the base and decide in planning whether to append time-of-day / weekday detail and which `lang` to use (recipient's stored language).
- `POST /api/my/post/{id}/reschedule` → body `{local_datetime}`; tz from `get_user_timezone(user_id) or "UTC"`; `parse_local_datetime` → `validate_schedule_time`; on valid call `update_editable_post_time(id, user_id, scheduled_at_utc=...)`. 400 on unparseable/invalid/past time, 404 if not owned/editable.
- `POST /api/my/post/{id}/cancel` → `cancel_post(user_id, id)`; 404 if not owned/pending.
- `POST /api/my/recurring/{id}/cancel` → `cancel_recurring_pattern(user_id, id)`; 404 if not owned.

### Frontend (`core/webapp_static/queue.html` — new)
- Two sections: pending one-off posts and active recurring patterns.
- Per one-off: destination title, local time, kind (text/media + count), truncated preview; actions **Reschedule** (local date/time input) + **Cancel** (with confirm).
- Per pattern: destination, interval description, next instance local time; action **Cancel series** (with confirm).
- Reuse admin.html's theme-aware CSS + inline-SVG style; render demo data when opened outside Telegram (same trick as admin.html). Send initData via `Authorization: tma <initData>` header. A small `apiPost` helper mirrors admin.html.

### Bot entry points (`telegram/handlers/`)
- New `/app` command → sends a `web_app` button opening `WEBAPP_URL/app` (mirror `telegram/admin.py`'s admin button). i18n label.
- `/queue` output → append the same `web_app` button below the existing text list (the text list remains the fallback).
- Both only rendered when `WEBAPP_URL` is set.

## Verification

- Webapp tests: `_require_user` accepts valid initData, rejects bad signature / stale `auth_date`; `GET /api/my/queue` and `/api/my/recurring` return only the caller's rows; reschedule/cancel/recurring-cancel on a row owned by another user → 404 (ownership enforced at the route via server-derived `user_id`, double-guarded by the DAL); unparseable/past reschedule time → 400; recurring cancel flips `is_active=0` and cancels pending instances.
- DAL is already covered; add route-layer ownership + validation tests specifically.
- Manual: open from two different Telegram accounts, confirm each sees only their own queue + patterns; reschedule respects the user's stored timezone; cancel removes the post so the scheduler no longer sends it; cancel-series stops future instances.
