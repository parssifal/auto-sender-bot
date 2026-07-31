# Design: Admin Broadcast to User DMs

**Date:** 2026-07-31
**Status:** Draft — ready for planning (open decisions marked ⚠)
**Scope:** New feature — admin sends one message to every registered user's private chat, with a delivery report.
**Scale:** 1–10 users (personal/team), SQLite, single-process.
**Roadmap:** Item 1 of `docs/superpowers/2026-07-31-roadmap-post-refactor.md`. Plan to be written in a later session.

---

## Problem

There is no way to reach all users at once. The admin Mini App is read-only (`core/webapp.py` exposes only `GET` routes). Broadcasting to user DMs was explicitly deferred in `2026-07-28-admin-mini-app-and-preview-design.md` (§Out of scope). The pieces it needs now exist.

## Goals

1. An admin composes a message once; the bot delivers it to every user's private chat (`chat_id == user.id`).
2. Per-recipient failures (user blocked the bot, etc.) are tolerated and reported, never abort the run.
3. A delivery summary is returned: `{delivered, blocked, failed}`.
4. First **write** endpoint through the webapp — done with auth + explicit confirm discipline.

## Non-Goals

- No scheduling of broadcasts (send-now only in v1).
- No per-segment targeting / filters (all users; segmentation is a later idea).
- No rich campaign analytics beyond the delivery summary.
- No change to the existing scheduled-post pipeline (`create_broadcast_posts` is for scheduled *channel* posts and is unrelated).

## Current-State Grounding

- **Auth:** `validate_init_data(init_data, bot_token)` → user dict or `None`; `_require_admin(request)` in `core/webapp.py` extracts initData, validates, and checks membership in `admin_set` (from `ADMIN_IDS`). Reuse verbatim for the new route.
- **Send primitives:** `core/notifier.send_text(bot, chat_id, text, entities=...)` and `send_media_post(...)` already send to an arbitrary `chat_id`. A DM is just `chat_id = user.id`.
- **Recipients:** `users` table (`id, language, username, first_name, created_at`); `StateStore.list_users(limit, offset)` enumerates them. Per-user `language` enables optional localization.
- **Error surface:** `send_text` may raise `aiogram.exceptions.TelegramForbiddenError` when the user has blocked the bot — must be caught per-recipient.

## Architecture

### DAL (`core/state.py`)
- Add `all_user_ids() -> list[int]` (single cheap `SELECT id FROM users`), or page via existing `list_users`. ⚠ Decide: plain id list vs. `(id, language)` tuples if we localize.
- ⚠ Optional: `broadcast_runs` / `broadcast_deliveries` tables to persist history + per-user status. **Recommendation:** skip in v1 (YAGNI at this scale); return the summary in the HTTP response only. Add later if an audit trail is wanted.

### Service (`core/services/admin_broadcast_svc.py`)
Pure orchestration (no Telegram-API import beyond the passed `bot`, no raw SQL — matches the Phase 3 service boundary; here it does need `bot` since sending IS the job — document this as a deliberate exception, or inject a `send` callable to keep it testable without aiogram).
```
async def broadcast_to_all(store, bot, *, text, entities=None) -> dict:
    recipients = await store.all_user_ids()
    delivered = blocked = failed = 0
    for uid in recipients:
        try:
            await notifier.send_text(bot, uid, text, entities=entities)
            delivered += 1
        except TelegramForbiddenError:
            blocked += 1
        except Exception:
            failed += 1
        await asyncio.sleep(<throttle>)      # respect Telegram ~30 msg/s
    return {"total": len(recipients), "delivered": delivered, "blocked": blocked, "failed": failed}
```
⚠ Throttle: a fixed `asyncio.sleep(0.05)` or a semaphore. At ≤10 users this is a non-issue; keep it simple.
- **Testability:** inject the send function (default `notifier.send_text`) so unit tests pass a fake and assert the summary without a real bot.

### Webapp (`core/webapp.py`)
- `POST /api/broadcast` — `_require_admin` gate; JSON body `{text, entities?}`; validate non-empty text; call the service; return the summary JSON.
- ⚠ Sync vs background: at this scale, send synchronously inside the request. If the user base ever grows, move to an `asyncio.create_task` + a `GET /api/broadcast/{id}/status` poller (out of scope for v1).

### Frontend (`core/webapp_static/admin.html`)
- A "Рассылка" card: textarea + char counter + "Отправить всем" button.
- **Confirm step** (first destructive admin action): a modal showing recipient count + message preview before the POST.
- Result panel rendering `{delivered, blocked, failed}`.

## Decisions / Open Questions (⚠ for planning)

- ⚠ **Media support:** text-only v1, or also photo/video? (Text-only recommended for v1; media reuses `send_media_post`.)
- ⚠ **Localization:** send one text as-is, or template per `users.language`? (One text v1; per-language is a later enhancement.)
- ⚠ **Persist history?** No table in v1 (recommendation).
- ⚠ **Alternative entry point:** also expose as an `/admin_broadcast` bot command? (Panel-only v1; command is a small add.)

## Verification

- Service unit tests with a fake `send` (delivered/blocked/failed accounting; `TelegramForbiddenError` counted as blocked, other exceptions as failed; empty recipient set → zeros).
- Webapp test: `POST /api/broadcast` forbidden without admin initData (403); happy path returns summary.
- Manual: compose in panel, confirm, verify DMs arrive and summary matches; block the bot from a test account and confirm it's counted as `blocked`, not a crash.
