# Admin Broadcast to User DMs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin compose one text message and deliver it to every registered user's private chat, with a `{delivered, blocked, failed}` report, exposed both through the admin Mini App and a `/admin_broadcast` bot command.

**Architecture:** New pure service `core/services/admin_broadcast_svc.py` orchestrates per-recipient sends with an injectable `send` callable (default `notifier.send_text`), tolerating per-user failures. A new DAL method `all_user_ids()` enumerates recipients. The webapp gains its first **write** endpoint `POST /api/broadcast` (admin-gated, needs `bot` threaded in). A `/admin_broadcast` FSM flow in `telegram/admin.py` provides a second entry point with an explicit confirm step. No new tables — the summary lives only in the HTTP/command response.

**Tech Stack:** Python 3.10+, aiogram 3.25 (FSM, `TelegramForbiddenError`), aiosqlite, aiohttp webapp, vanilla-JS `admin.html`, pytest / pytest-asyncio.

**Decisions locked (from spec + user):** text-only v1; entry points = panel **and** `/admin_broadcast`; **no** history persistence (summary in response only); throttle = fixed `asyncio.sleep(0.05)`.

**Spec:** `docs/superpowers/specs/2026-07-31-admin-broadcast-design.md`

---

## File Structure

- **Create** `core/services/admin_broadcast_svc.py` — `broadcast_to_all(store, bot, *, text, entities_json=None, send=None, throttle=0.05) -> dict`. Pure orchestration; only `core.*` imports + the injected `send`. Deliberate boundary exception (documented in a module docstring): it receives `bot` because sending IS the job, but never imports aiogram send primitives directly — it calls `notifier.send_text`, which is `core.*`.
- **Modify** `core/state.py` — add `all_user_ids() -> list[int]`.
- **Modify** `core/webapp.py` — add `bot: Bot | None = None` param to `start_webapp_server`; add `POST /api/broadcast`.
- **Modify** `main.py` — pass `bot=bot` into `start_webapp_server`.
- **Modify** `core/webapp_static/admin.html` — "Рассылка" card: textarea + counter + button, confirm modal (recipient count + preview), result panel.
- **Modify** `telegram/admin.py` — `AdminBroadcastStates` + `/admin_broadcast` FSM flow (collect → confirm → send → report).
- **Modify** `telegram/handlers/states.py` — add `AdminBroadcastStates` (keep all StatesGroups in one place, matching the existing convention).
- **Modify** `telegram/i18n.py` — `admin_broadcast_*` keys in `en` + `ru` (others fall back to `en`; key MUST exist in `en` or `tr` raises KeyError).
- **Tests:** `tests/test_admin_broadcast_svc.py` (new), extend `tests/test_state_users.py`, `tests/test_webapp_server.py`, `tests/test_webapp_page.py`, `tests/test_router_admin.py`.

**Boundary note:** `tests/test_services_boundary.py` AST-guards that services import only `core.*`. The new service imports `core.notifier` and `core.state` only — compliant. Verify this guard still passes after Task 2.

---

## Task 1: DAL — `all_user_ids()`

**Files:**
- Modify: `core/state.py` (near `list_users`, ~line 371)
- Test: `tests/test_state_users.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_state_users.py — add
@pytest.mark.asyncio
async def test_all_user_ids_returns_every_id(store):
    await store.ensure_user(1)
    await store.ensure_user(2)
    await store.ensure_user(3)
    ids = await store.all_user_ids()
    assert sorted(ids) == [1, 2, 3]
    assert all(isinstance(i, int) for i in ids)


@pytest.mark.asyncio
async def test_all_user_ids_empty(store):
    assert await store.all_user_ids() == []
```

Reuse the existing `store` fixture in that file (check its name; mirror the fixture used by neighbouring tests — an in-memory `StateStore` whose `_conn` is closed on teardown).

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_state_users.py::test_all_user_ids_returns_every_id -q`
Expected: FAIL — `AttributeError: 'StateStore' object has no attribute 'all_user_ids'`.

- [ ] **Step 3: Implement**

```python
# core/state.py — add as a method on StateStore, next to list_users
async def all_user_ids(self) -> list[int]:
    async with self._conn.execute("SELECT id FROM users ORDER BY id") as cur:
        rows = await cur.fetchall()
    return [int(r["id"]) for r in rows]
```

Match the exact cursor/row idiom already used in `list_users` / `count_users` in this file (row access style, `self._conn` vs a helper). Adjust `r["id"]` if the file uses tuple rows.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_state_users.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/state.py tests/test_state_users.py
git commit -m "feat(dal): add all_user_ids() for broadcast recipient enumeration"
```

---

## Task 2: Service — `broadcast_to_all`

**Files:**
- Create: `core/services/admin_broadcast_svc.py`
- Test: `tests/test_admin_broadcast_svc.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_admin_broadcast_svc.py
import pytest
import pytest_asyncio
from aiogram.exceptions import TelegramForbiddenError

from core.db import open_db
from core.services import admin_broadcast_svc
from core.state import StateStore


@pytest_asyncio.fixture
async def store():
    conn = await open_db(":memory:")
    state = StateStore(conn)
    await state.migrate()
    yield state
    await conn.close()


class _FakeBot:  # never actually called when send is injected
    pass


@pytest.mark.asyncio
async def test_broadcast_accounts_delivered_blocked_failed(store):
    for uid in (1, 2, 3, 4):
        await store.ensure_user(uid)
    calls = []

    async def fake_send(uid):
        calls.append(uid)
        if uid == 2:
            raise TelegramForbiddenError(method=None, message="bot was blocked by the user")
        if uid == 3:
            raise RuntimeError("network")

    summary = await admin_broadcast_svc.broadcast_to_all(
        store, _FakeBot(), text="hi", send=fake_send, throttle=0,
    )
    assert summary == {"total": 4, "delivered": 2, "blocked": 1, "failed": 1}
    assert sorted(calls) == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_broadcast_empty_recipients(store):
    async def fake_send(uid):  # pragma: no cover — must not be called
        raise AssertionError("should not send")

    summary = await admin_broadcast_svc.broadcast_to_all(
        store, _FakeBot(), text="hi", send=fake_send, throttle=0,
    )
    assert summary == {"total": 0, "delivered": 0, "blocked": 0, "failed": 0}


@pytest.mark.asyncio
async def test_broadcast_default_send_uses_notifier(store, monkeypatch):
    await store.ensure_user(5)
    seen = {}

    async def fake_send_text(bot, chat_id, text, entities_json):
        seen["args"] = (chat_id, text, entities_json)

    monkeypatch.setattr(admin_broadcast_svc.notifier, "send_text", fake_send_text)
    summary = await admin_broadcast_svc.broadcast_to_all(
        store, _FakeBot(), text="hello", entities_json='[]', throttle=0,
    )
    assert summary["delivered"] == 1
    assert seen["args"] == (5, "hello", '[]')
```

> If `TelegramForbiddenError(method=None, message=...)` fails to construct under the installed aiogram version, fall back to `TelegramForbiddenError(method="sendMessage", message="blocked")` or construct via the pattern used in `tests/` for scheduler errors — check an existing test that raises it before finalizing.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_admin_broadcast_svc.py -q`
Expected: FAIL — `ModuleNotFoundError: core.services.admin_broadcast_svc`.

- [ ] **Step 3: Implement**

```python
# core/services/admin_broadcast_svc.py
"""Admin broadcast to user DMs.

Boundary exception (Phase 3 rule = services import only ``core.*``): this service
receives the aiogram ``bot`` because delivery IS its job, but it never imports
aiogram send primitives — it delegates to ``core.notifier.send_text`` (a ``core.*``
module). The ``send`` param is injectable so unit tests need no real bot.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from core import notifier
from core.state import StateStore


async def broadcast_to_all(
    store: StateStore,
    bot: Bot,
    *,
    text: str,
    entities_json: str | None = None,
    send: Callable[[int], Awaitable[object]] | None = None,
    throttle: float = 0.05,
) -> dict:
    """Send ``text`` to every user's DM. Per-recipient failures never abort the run.

    Returns ``{"total", "delivered", "blocked", "failed"}``. ``TelegramForbiddenError``
    (user blocked the bot) is counted as ``blocked``; any other exception as ``failed``.
    """
    async def _default_send(uid: int) -> object:
        return await notifier.send_text(bot, uid, text, entities_json)

    _send = send or _default_send

    recipients = await store.all_user_ids()
    delivered = blocked = failed = 0
    for uid in recipients:
        try:
            await _send(uid)
            delivered += 1
        except TelegramForbiddenError:
            blocked += 1
        except Exception:
            failed += 1
        if throttle:
            await asyncio.sleep(throttle)
    return {
        "total": len(recipients),
        "delivered": delivered,
        "blocked": blocked,
        "failed": failed,
    }
```

- [ ] **Step 4: Run to verify it passes + boundary guard holds**

Run: `.venv/bin/pytest tests/test_admin_broadcast_svc.py tests/test_services_boundary.py -q`
Expected: PASS (both). If the boundary guard flags `aiogram` imports, confirm the guard only forbids non-`core` **project** modules / raw SQL — `aiogram` is a third-party dep like in other services; read the guard and align. If the guard truly forbids aiogram, inject `bot` type as `object` and drop the `from aiogram import Bot` type-only import.

- [ ] **Step 5: Commit**

```bash
git add core/services/admin_broadcast_svc.py tests/test_admin_broadcast_svc.py
git commit -m "feat(service): admin_broadcast_svc.broadcast_to_all with injectable send"
```

---

## Task 3: Webapp — thread `bot` + `POST /api/broadcast`

**Files:**
- Modify: `core/webapp.py` (`start_webapp_server` signature ~117; routes ~163)
- Modify: `main.py` (call site ~57)
- Test: `tests/test_webapp_server.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_webapp_server.py — add near the other server tests
class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


@pytest_asyncio.fixture
async def bcast_server(store: StateStore):
    bot = _FakeBot()
    # seed a couple of ordinary users to receive the broadcast
    await store.ensure_user(101)
    await store.ensure_user(102)
    srv = await start_webapp_server(
        host="127.0.0.1", port=0, store=store,
        bot_token=TOKEN, admin_ids=(ADMIN_ID,), bot=bot,
    )
    srv._fake_bot = bot  # stash for assertions
    yield srv
    await srv.close()


@pytest.mark.asyncio
async def test_broadcast_forbidden_without_admin(bcast_server):
    url = f"http://{bcast_server.host}:{bcast_server.port}/api/broadcast"
    async with ClientSession() as s:
        async with s.post(url, json={"text": "hi"},
                          headers={"Authorization": _init_data(NON_ADMIN_ID)}) as r:
            assert r.status == 403


@pytest.mark.asyncio
async def test_broadcast_rejects_empty_text(bcast_server):
    url = f"http://{bcast_server.host}:{bcast_server.port}/api/broadcast"
    async with ClientSession() as s:
        async with s.post(url, json={"text": "   "},
                          headers={"Authorization": _init_data(ADMIN_ID)}) as r:
            assert r.status == 400


@pytest.mark.asyncio
async def test_broadcast_happy_path_returns_summary(bcast_server):
    url = f"http://{bcast_server.host}:{bcast_server.port}/api/broadcast"
    async with ClientSession() as s:
        async with s.post(url, json={"text": "hello all"},
                          headers={"Authorization": _init_data(ADMIN_ID)}) as r:
            assert r.status == 200
            body = await r.json()
    # store fixture seeds ADMIN_ID (42) + this fixture adds 101,102 => total 3
    assert body["total"] == 3
    assert body["delivered"] == 3
    assert body["blocked"] == 0
    assert body["failed"] == 0
```

> Confirm the seeded user count: the shared `store` fixture already `ensure_user(ADMIN_ID)`. `all_user_ids()` includes the admin (admins are users too). If that's undesirable product-wise, it's still fine for v1 — the admin receiving their own broadcast is acceptable; note it and move on.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_webapp_server.py -k broadcast -q`
Expected: FAIL — `start_webapp_server() got an unexpected keyword argument 'bot'`.

- [ ] **Step 3: Implement**

In `core/webapp.py`:

```python
# add import at top
from aiogram import Bot

# signature
async def start_webapp_server(
    *,
    host: str,
    port: int,
    store: StateStore,
    bot_token: str,
    admin_ids: tuple[int, ...],
    bot: Bot | None = None,
) -> WebappServer:
    ...
```

Add the handler (inside `start_webapp_server`, alongside the others), importing the service at top of file (`from core.services import admin_broadcast_svc`):

```python
    async def api_broadcast(request: web.Request) -> web.Response:
        if _require_admin(request) is None:
            return web.json_response({"error": "forbidden"}, status=403)
        if bot is None:
            return web.json_response({"error": "bot_unavailable"}, status=503)
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "bad_request"}, status=400)
        text = str(payload.get("text") or "").strip()
        if not text:
            return web.json_response({"error": "empty_text"}, status=400)
        entities_json = payload.get("entities_json")
        summary = await admin_broadcast_svc.broadcast_to_all(
            store, bot, text=text, entities_json=entities_json,
        )
        return web.json_response(summary)
```

Register it: `app.router.add_post("/api/broadcast", api_broadcast)` next to the other `add_*` calls.

In `main.py`, pass the bot:

```python
            webapp_server = await start_webapp_server(
                host=cfg.webapp_host,
                port=cfg.webapp_port,
                store=store,
                bot_token=cfg.bot_token,
                admin_ids=cfg.admin_ids,
                bot=bot,
            )
```

- [ ] **Step 4: Run to verify it passes (and nothing else broke)**

Run: `.venv/bin/pytest tests/test_webapp_server.py tests/test_webapp_auth.py -q`
Expected: PASS. The existing `server` fixture omits `bot=` — that's fine (default `None`); only the broadcast route needs it.

- [ ] **Step 5: Commit**

```bash
git add core/webapp.py main.py tests/test_webapp_server.py
git commit -m "feat(webapp): POST /api/broadcast admin write endpoint"
```

---

## Task 4: i18n keys for the command flow

**Files:**
- Modify: `telegram/i18n.py` (`en` block ~323, `ru` block ~686 — put keys next to `admin_intro`)

- [ ] **Step 1: Add keys to `en` AND `ru`** (key must exist in `en` or `tr` raises KeyError; `ru` is the admin's language; other langs fall back to `en`).

```python
# en
"admin_broadcast_prompt": "Send the text to broadcast to ALL users. /cancel to abort.",
"admin_broadcast_empty": "Empty message. Send some text, or /cancel.",
"admin_broadcast_confirm": "Broadcast this to {count} user(s)?\n\n———\n{preview}",
"admin_broadcast_confirm_btn": "Send to all",
"admin_broadcast_cancel_btn": "Cancel",
"admin_broadcast_sending": "Sending…",
"admin_broadcast_report": "Done. Delivered: {delivered}, blocked: {blocked}, failed: {failed} (of {total}).",
"admin_broadcast_cancelled": "Broadcast cancelled.",
```

```python
# ru
"admin_broadcast_prompt": "Отправьте текст для рассылки ВСЕМ пользователям. /cancel — отмена.",
"admin_broadcast_empty": "Пустое сообщение. Пришлите текст или /cancel.",
"admin_broadcast_confirm": "Разослать это {count} пользователю(ям)?\n\n———\n{preview}",
"admin_broadcast_confirm_btn": "Отправить всем",
"admin_broadcast_cancel_btn": "Отмена",
"admin_broadcast_sending": "Отправляю…",
"admin_broadcast_report": "Готово. Доставлено: {delivered}, заблокировали: {blocked}, ошибок: {failed} (из {total}).",
"admin_broadcast_cancelled": "Рассылка отменена.",
```

- [ ] **Step 2: Sanity-check no KeyError**

Run: `.venv/bin/python -c "from telegram.i18n import tr; print(tr('ru','admin_broadcast_report',delivered=1,blocked=0,failed=0,total=1)); print(tr('de','admin_broadcast_prompt'))"`
Expected: prints the ru report line and the en prompt (de fallback). No traceback.

- [ ] **Step 3: Commit**

```bash
git add telegram/i18n.py
git commit -m "i18n: admin_broadcast_* strings (en, ru)"
```

---

## Task 5: Bot command `/admin_broadcast`

**Files:**
- Modify: `telegram/handlers/states.py` (add `AdminBroadcastStates`)
- Modify: `telegram/admin.py` (FSM flow; handlers get `state: FSMContext` and `bot: Bot` via aiogram DI)
- Test: `tests/test_router_admin.py`

- [ ] **Step 1: Write the failing test**

Inspect `tests/test_router_admin.py` first to reuse its harness (how it builds the admin router, fakes `Message`, drives handlers). Then add a flow test asserting:
- non-admin `/admin_broadcast` → no response (silent, like `/admin`);
- admin `/admin_broadcast` → prompt sent, state = `AdminBroadcastStates.collecting`;
- sending text → confirm message with inline confirm/cancel buttons, state = `confirming`;
- pressing confirm → service invoked (monkeypatch `admin_broadcast_svc.broadcast_to_all` to return a fixed summary) → report contains the delivered count, state cleared.

Model the test structure on the existing admin/router tests; if the harness there only tests `cmd_admin` at message level, follow the same fidelity (don't invent a callback-query harness heavier than the file's convention — a message-level assertion on prompt + state transition plus a unit-level assertion that confirm calls the service is sufficient).

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_router_admin.py -k broadcast -q`
Expected: FAIL — handler/states not defined.

- [ ] **Step 3: Implement**

`telegram/handlers/states.py`:

```python
class AdminBroadcastStates(StatesGroup):
    collecting = State()
    confirming = State()
```

`telegram/admin.py` — add inside `build_admin_router` (imports: `from aiogram.fsm.context import FSMContext`, `from aiogram import Bot, F`, `from aiogram.types import CallbackQuery`, `from telegram.handlers.states import AdminBroadcastStates`, `from core.services import admin_broadcast_svc`). Store the pending text in FSM data; build confirm keyboard with callbacks `abc:go` / `abc:no`.

```python
    @router.message(Command("admin_broadcast"))
    async def cmd_admin_broadcast(message: Message, state: FSMContext) -> None:
        user = message.from_user
        if user is None or user.id not in admin_set:
            return
        lang = await store.get_user_language(user.id)
        await state.set_state(AdminBroadcastStates.collecting)
        await message.answer(tr(lang, "admin_broadcast_prompt"))

    @router.message(AdminBroadcastStates.collecting, Command("cancel"))
    async def abc_cancel_collect(message: Message, state: FSMContext) -> None:
        lang = await store.get_user_language(message.from_user.id)
        await state.clear()
        await message.answer(tr(lang, "admin_broadcast_cancelled"))

    @router.message(AdminBroadcastStates.collecting)
    async def abc_collect(message: Message, state: FSMContext) -> None:
        lang = await store.get_user_language(message.from_user.id)
        text = (message.text or "").strip()
        if not text:
            await message.answer(tr(lang, "admin_broadcast_empty"))
            return
        entities_json = message.entities and __import__("json").dumps(
            [e.model_dump(exclude_none=True) for e in message.entities]
        ) or None
        await state.update_data(bcast_text=text, bcast_entities_json=entities_json)
        await state.set_state(AdminBroadcastStates.confirming)
        count = len(await store.all_user_ids())
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=tr(lang, "admin_broadcast_confirm_btn"), callback_data="abc:go"),
            InlineKeyboardButton(text=tr(lang, "admin_broadcast_cancel_btn"), callback_data="abc:no"),
        ]])
        preview = text if len(text) <= 500 else text[:500] + "…"
        await message.answer(tr(lang, "admin_broadcast_confirm", count=count, preview=preview), reply_markup=kb)

    @router.callback_query(AdminBroadcastStates.confirming, F.data == "abc:no")
    async def abc_no(cq: CallbackQuery, state: FSMContext) -> None:
        lang = await store.get_user_language(cq.from_user.id)
        await state.clear()
        await cq.message.edit_text(tr(lang, "admin_broadcast_cancelled"))
        await cq.answer()

    @router.callback_query(AdminBroadcastStates.confirming, F.data == "abc:go")
    async def abc_go(cq: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        lang = await store.get_user_language(cq.from_user.id)
        data = await state.get_data()
        await state.clear()
        await cq.message.edit_text(tr(lang, "admin_broadcast_sending"))
        await cq.answer()
        summary = await admin_broadcast_svc.broadcast_to_all(
            store, bot,
            text=data.get("bcast_text", ""),
            entities_json=data.get("bcast_entities_json"),
        )
        await cq.message.answer(tr(lang, "admin_broadcast_report", **summary))
```

Notes for the implementer:
- Prefer a module-level `import json` over the inline `__import__` hack shown above — the sketch avoids reordering imports; clean it up.
- The admin router is included AFTER the main feature router in `main.py`. Confirm the `/admin_broadcast` message doesn't get swallowed by a broader handler in the main router (commands generally aren't; `/admin` already coexists). If there's a catch-all, register order / filters may need a check — verify by running the flow test.
- These handlers gate on `admin_set`; the collecting/confirming states are only reachable after the admin-gated command, so re-checking admin on each step is belt-and-suspenders (optional). Do NOT leave the flow reachable by non-admins.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_router_admin.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add telegram/handlers/states.py telegram/admin.py tests/test_router_admin.py
git commit -m "feat(admin): /admin_broadcast FSM flow (collect, confirm, send, report)"
```

---

## Task 6: Frontend — "Рассылка" card in admin.html

**Files:**
- Modify: `core/webapp_static/admin.html`
- Test: `tests/test_webapp_page.py` (markup presence, matching the file's existing assertion style)

- [ ] **Step 1: Write the failing test**

Inspect `tests/test_webapp_page.py` — it likely asserts substrings exist in the served HTML. Add an assertion that the page contains the broadcast card anchor (e.g. an id `broadcast-card` and the button label text). Keep it as light as the existing checks.

```python
# tests/test_webapp_page.py — add
def test_admin_page_has_broadcast_card(page_html):  # reuse the file's html-loading fixture/helper
    assert 'id="broadcast-card"' in page_html
    assert "Отправить всем" in page_html
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_webapp_page.py -k broadcast -q`
Expected: FAIL — markup absent.

- [ ] **Step 3: Implement**

Add a card to `admin.html` following the existing card markup/JS conventions in that file (match class names, theme variables, and the `authFetch`/init-data header helper already used for `/api/stats`). Behaviour:
- Textarea (`#broadcast-text`) + live char counter.
- "Отправить всем" button (`#broadcast-send`) → opens a **confirm modal** showing recipient count (read from already-loaded stats `total_users`, or a `GET /api/users` length) + a message preview.
- Modal confirm → `POST /api/broadcast` with `{text}` via the existing authorized-fetch helper (must send the `Authorization: tma <initData>` header the other calls use).
- Render `{delivered, blocked, failed, total}` in a result panel; disable the button while the request is in flight; re-enable after.
- On non-200, show the error (`forbidden` / `empty_text` / `bot_unavailable`).

Keep it vanilla JS + inline styles consistent with the rest of the file. No external assets (page is self-contained).

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_webapp_page.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/webapp_static/admin.html tests/test_webapp_page.py
git commit -m "feat(webapp-ui): broadcast card with confirm modal + result panel"
```

---

## Task 7: Full suite, docs, memory

- [ ] **Step 1: Run the whole suite + pyflakes**

Run: `.venv/bin/pytest -q && .venv/bin/pyflakes core telegram main.py`
Expected: all green (was 264 before this work; expect 264 + new tests), pyflakes clean.

- [ ] **Step 2: Manual smoke (documented, run if a bot token is available)**
- Open the admin Mini App → Рассылка → type text → confirm → verify DM arrives to a test account; verify the result panel matches.
- Block the bot from a second test account → broadcast → confirm it's counted as `blocked`, not a crash.
- `/admin_broadcast` in chat → prompt → text → confirm → report line.
- Non-admin `/admin_broadcast` → silence.

- [ ] **Step 3: Mark the spec implemented**

Edit `docs/superpowers/specs/2026-07-31-admin-broadcast-design.md` header `Status:` → `Implemented (feature/admin-broadcast, 2026-07-31)`. Resolve the ⚠ open questions with the chosen answers (text-only; panel + command; no history; throttle 0.05).

- [ ] **Step 4: Update project memory**

Append to `admin_miniapp` memory: broadcast shipped — `POST /api/broadcast` (first write endpoint), `admin_broadcast_svc`, `all_user_ids()`, `/admin_broadcast` command, no history table.

- [ ] **Step 5: Commit + finish branch**

```bash
git add -A
git commit -m "docs: mark admin-broadcast spec implemented; update memory"
```

Then use superpowers:finishing-a-development-branch to decide merge/PR.

---

## Verification Summary (maps to spec §Verification)

- Service unit tests: delivered/blocked/failed accounting, `TelegramForbiddenError`→blocked, other→failed, empty recipients→zeros, default send hits `notifier.send_text`. — Task 2
- Webapp: 403 without admin initData; 400 empty text; happy path returns summary. — Task 3
- Command flow: admin-only, collect→confirm→send→report. — Task 5
- Frontend markup + confirm discipline. — Task 6
- Manual: DM arrival, blocked accounting, no crash. — Task 7
