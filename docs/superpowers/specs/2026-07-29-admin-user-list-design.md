# Admin User List + Per-User Stats — Design Spec

**Date:** 2026-07-29
**Status:** Approved (design)
**Branch (suggested):** `feature/admin-user-list`
**Related:** [admin Mini App spec](2026-07-28-admin-mini-app-and-preview-design.md)

## Problem

The admin Mini App (`core/webapp_static/admin.html`, served by `core/webapp.py`)
shows aggregate counts like "3 users" but gives the admin no way to **enumerate**
the users or discover their Telegram ids. The page already has a "find user by
id" box (`#findCard`) and a "top active users" block, but nothing lets the admin
learn an id in the first place. As a result per-user drill-down is unusable.

This is the first of two specs. A second, separate spec will cover a **user-facing**
Mini App (view queue / reschedule / cancel). This spec is admin-only.

## Goals

- List every user in the admin panel with their numeric Telegram id (the missing piece).
- Show a human-friendly label (`@username` / first name) where available.
- Click a user to open the existing per-user detail card.
- Start capturing `username` / `first_name` so labels populate going forward.

## Non-goals

- No user-facing panel (separate spec).
- No write operations from the admin panel (broadcast is already deferred).
- No backfill of names for users who never interact again — names populate only
  on the next interaction after deploy.
- No pagination UI work beyond a simple limit/offset in the query (scale is 1–10 users).

## Design

### 1. Data layer (`core/state.py`)

**Migration.** Add two nullable columns to the `users` table:

```sql
ALTER TABLE users ADD COLUMN username TEXT NULL;
ALTER TABLE users ADD COLUMN first_name TEXT NULL;
```

Follow the project's current schema approach (in-place `CREATE TABLE` + additive
`ALTER` guarded so re-running `migrate()` is safe — mirror how existing optional
columns are handled). Versioned migrations (Phase 2 of the refactor plan) are out
of scope here.

**`ensure_user` capture.** Extend the signature, keeping new params optional so the
~20 existing call sites keep compiling:

```python
async def ensure_user(
    self,
    user_id: int,
    username: str | None = None,
    first_name: str | None = None,
) -> None:
```

The upsert updates the name fields with `COALESCE` so a later call without a name
does **not** wipe a previously stored one, while a call *with* a name updates it:

```sql
INSERT INTO users(user_id, timezone, language, username, first_name, created_at, updated_at)
VALUES(?, NULL, NULL, ?, ?, ?, ?)
ON CONFLICT(user_id) DO UPDATE SET
    updated_at = excluded.updated_at,
    username   = COALESCE(excluded.username, users.username),
    first_name = COALESCE(excluded.first_name, users.first_name)
```

Update the main entry points that have `message.from_user` / `callback.from_user`
handy (at minimum `/start`) to pass `username` and `first_name`. Remaining call
sites can keep passing only the id; they simply won't refresh the label.

**`list_users`.** New read method:

```python
async def list_users(self, limit: int = 100, offset: int = 0) -> list[dict]:
    ...
```

Returns, per user: `user_id`, `username`, `first_name`, `language`, `created_at`,
`last_active` (from `updated_at`), `posts` (count of that user's `scheduled_posts`),
`channels` (count of that user's destinations). Sorted by `last_active` descending,
then `user_id` ascending. `limit`/`offset` exist for safety but at current scale a
single query returns everyone.

**`get_user_profile` enrichment.** Add `username` and `first_name` to the returned
dict, plus a per-status breakdown of the user's posts (`pending` / `sent` / `failed`)
alongside the existing total `posts`.

### 2. API (`core/webapp.py`)

- New route `GET /api/users` guarded by the existing `_require_admin` check
  (Telegram `initData` in `Authorization` → `validate_init_data()` → id in
  `ADMIN_IDS`). Returns `{ "users": [ ...list_users... ] }`.
- `GET /api/user/{id}` is unchanged structurally; it just returns the extra
  `username` / `first_name` / status-breakdown fields now present in
  `get_user_profile`.

### 3. Frontend (`core/webapp_static/admin.html`)

- New card **«Все пользователи»**, placed between the existing "Топ активных"
  card and the "find user by id" card. Loaded from `GET /api/users` on dashboard
  load, using the same `initData` `Authorization` header as `/api/stats`.
- Each row:
  - Label: `@username`, else first name, else `ID <n>`.
  - Below the label: the numeric **id** in a small monospace style with a
    copy-to-clipboard affordance.
  - Right side: compact counters (posts · channels) and last-active date.
- Clicking a row reuses the existing detail card (`#findCard`): set the id, call
  `/api/user/{id}`, render timezone / language / created-at / channels and the
  `pending / sent / failed` post breakdown. The manual id search box stays as-is.
- Match existing file conventions: `esc()` for all user-controlled strings
  (username/first_name), graceful empty-list state, and demo-data rendering when
  the page is opened outside Telegram (as the rest of the dashboard already does).

### 4. Testing

- **`list_users`**: population, sort order (last-active desc), limit/offset,
  presence of `username`/`first_name`.
- **`ensure_user` with name**: first write stores name; a later call *without* a
  name preserves the stored one (COALESCE); a later call *with* a new name updates it.
- **`get_user_profile`**: new fields present; status breakdown correct.
- **API `/api/users`**: 403 without valid `initData` / for a non-admin id; 200 and
  correct shape for an admin.
- **Gotcha:** DAL tests that create a `StateStore` MUST close `store._conn` — an
  unclosed aiosqlite connection hangs pytest-asyncio teardown. `mark_sent` only
  transitions `sending`→`sent`, so tests needing sent posts must
  `claim_post_for_sending` first.

## Risks / notes

- Name capture is forward-only; existing users show as `ID <n>` until they next
  interact. Acceptable — the numeric id (the actual ask) is always available.
- No new dependencies; `admin.html` stays a single self-contained vanilla-JS file.
- Auth surface is unchanged — the new endpoint reuses `_require_admin`.
