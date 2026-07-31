# User-Facing Mini App (Queue / Reschedule / Cancel + Recurring) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a per-user Telegram Mini App where any user views their pending one-off posts + active recurring patterns and can reschedule/cancel one-offs and cancel recurring series.

**Architecture:** Reuse the admin Mini App plumbing (aiohttp + Telegram `initData` + single-file HTML). Add a non-admin auth gate `_require_user` (valid initData, no `ADMIN_IDS` check); every user route derives `user_id` server-side and passes it to the already-user-scoped DAL. New `GET /app` serves `core/webapp_static/queue.html`. Two bot entry points open it: a new `/app` command and a `web_app` button appended to `/queue`. Interval labels render client-side (core must never import telegram).

**Tech Stack:** Python 3.10+, aiogram 3.25, aiohttp, aiosqlite, pytest/pytest-asyncio. Frontend: vanilla single-file HTML/CSS/JS (mirror `core/webapp_static/admin.html`).

**Spec:** `docs/superpowers/specs/2026-07-31-user-mini-app-queue-design.md`

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `core/webapp.py` | Add `_extract_user`/`_require_user`, 5 user routes, small JSON mappers | Modify |
| `core/webapp_static/queue.html` | User Mini App UI (queue + recurring, reschedule/cancel) | Create |
| `telegram/user_app.py` | `/app` command → `web_app` button (mirror `telegram/admin.py`) | Create |
| `telegram/i18n.py` | `app_intro`, `app_open_btn`, `app_not_configured` (en + ru) | Modify |
| `telegram/router.py` | Thread `webapp_url` into assembler → `queue.build_router` | Modify |
| `telegram/handlers/queue.py` | `build_router(store, *, webapp_url=None)`; append `web_app` button to `/queue` keyboard | Modify |
| `main.py` | Pass `cfg.webapp_url` to `build_router`; include `build_user_app_router` | Modify |
| `tests/test_webapp_user_api.py` | Route-layer auth/ownership/validation tests | Create |
| `tests/test_user_app_entry.py` | `/app` command + queue button wiring (light) | Create |

**Grounding facts (verified against the tree):**
- Auth: `validate_init_data(init_data, bot_token)` → user dict or `None`; `_extract_init_data(request)` reads `Authorization: tma <initData>` (`core/webapp.py:107`, `:21`). Existing `_require_admin` closure lives inside `start_webapp_server` (`:129`).
- DAL (all user-scoped): `list_editable_pending_posts(user_id, limit, offset)` → `list[ScheduledPostRow]` (`SELECT sp.*`, carries `chat_id`, NOT destination title); `get_destination_title(chat_id)` → `str | None` (state.py:1593); `get_post_media(post_id)` → `list[dict]`; `update_editable_post_time(post_id, user_id, *, scheduled_at_utc)` → `bool`; `cancel_post(user_id, post_id)` → `bool`; `list_user_recurring_summaries(user_id, *, offset, limit, include_inactive)` → `list[RecurringPatternSummary]`; `cancel_recurring_pattern(user_id, pattern_id)` → `bool` (transactional).
- `RecurringPatternSummary`: `.pattern` (`RecurringPattern` with `id, interval_type, weekdays_mask, time_of_day_minutes, timezone, ...`), `.destination_title`, `.destination_username`, `.next_post_id`, `.next_scheduled_at_utc`, `.next_post_status`.
- Time: `core/utils.parse_local_datetime(text, tz_name)` (format `"DD.MM.YYYY HH:MM"`) → `ParsedScheduleTime.utc_epoch`; `core/utils.validate_schedule_time(utc_timestamp, now_utc=None)` → `ScheduleTimeValidation(is_valid, error_key)`. Timezone: `store.get_user_timezone(user_id) or "UTC"` (mandatory fallback — method returns `str | None`).
- Entry-point pattern: `telegram/admin.py` — `InlineKeyboardButton(text=..., web_app=WebAppInfo(url=webapp_url))`, guarded by `if not webapp_url`.
- Test harness: `tests/test_webapp_server.py` — `_init_data(user_id, token=TOKEN)` builds signed initData; fixtures `store` (in-memory) + `server` (port 0); assertions via `aiohttp.ClientSession` against `server.url(path)` with `headers={"Authorization": _init_data(uid)}`.

**Layering rule:** `core/webapp.py` imports only `core.*` (verified: core never imports telegram). Interval-type → human label is rendered in `queue.html` JS, NOT server-side. The recurring payload returns raw `interval_type` + `time_of_day_minutes` + the envelope `lang`.

---

## Task 1: User auth gate + `GET /api/my/queue`

**Files:**
- Modify: `core/webapp.py` (inside `start_webapp_server`, near `_require_admin` at :129; register route near :183)
- Test: `tests/test_webapp_user_api.py` (create)

- [ ] **Step 1: Write failing tests**

Create `tests/test_webapp_user_api.py`. Reuse the harness shape from `tests/test_webapp_server.py` (copy `_init_data`, and the `store`/`server` fixtures, adapting the server fixture to seed two users). Keep `bot_token=TOKEN`; no `admin_ids` needed for these routes but the fixture still passes `admin_ids=(ADMIN_ID,)`.

```python
from __future__ import annotations

import hashlib, hmac, json, time
from urllib.parse import urlencode

import pytest, pytest_asyncio
from aiohttp import ClientSession

from core.db import open_db
from core.state import StateStore
from core.webapp import start_webapp_server

TOKEN = "123456:test-token"
USER_A = 111
USER_B = 222
CHAT_A = -3001


def _init_data(user_id: int, *, token: str = TOKEN) -> str:
    user = {"id": user_id, "first_name": "U"}
    fields = {
        "auth_date": str(int(time.time())),
        "query_id": "AAA",
        "user": json.dumps(user, separators=(",", ":")),
    }
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@pytest_asyncio.fixture
async def store() -> StateStore:
    conn = await open_db(":memory:")
    st = StateStore(conn)
    await st.migrate()
    for uid in (USER_A, USER_B):
        await st.ensure_user(uid)
        await st.set_user_language(uid, "ru")
    await st.upsert_destination(CHAT_A, "channel", "Channel A", None, "administrator", True)
    await st.link_user_destination(USER_A, CHAT_A, "link")
    await st.link_user_destination(USER_B, CHAT_A, "link")
    yield st
    await conn.close()


@pytest_asyncio.fixture
async def server(store: StateStore):
    srv = await start_webapp_server(
        host="127.0.0.1", port=0, store=store, bot_token=TOKEN, admin_ids=(999,),
    )
    yield srv
    await srv.close()


async def _mk_post(store: StateStore, user_id: int, *, when_offset: int = 3600) -> str:
    # Verified: create_scheduled_text_post(user_id, chat_id, scheduled_at_utc, text, entities_json) -> post_id (str)
    at = int(time.time()) + when_offset
    return await store.create_scheduled_text_post(user_id, CHAT_A, at, "hello", None)


async def _mk_recurring(store: StateStore, user_id: int) -> str:
    # Verified: create_recurring_pattern(user_id, chat_id, interval_type, time_of_day_minutes,
    #   timezone, start_at_utc, *, weekdays_mask=None, ...) -> pattern_id (str).
    # The destination (CHAT_A) must exist — list_user_recurring_summaries INNER JOINs destinations.
    at = int(time.time()) + 3600
    return await store.create_recurring_pattern(
        user_id=user_id, chat_id=CHAT_A, interval_type="daily",
        time_of_day_minutes=540, timezone="UTC", start_at_utc=at,
    )


@pytest.mark.asyncio
async def test_my_queue_requires_auth(server):
    async with ClientSession() as s:
        async with s.get(server.url("/api/my/queue")) as r:
            assert r.status == 403


@pytest.mark.asyncio
async def test_my_queue_bad_signature_forbidden(server):
    bad = _init_data(USER_A, token="999:wrong")
    async with ClientSession() as s:
        async with s.get(server.url("/api/my/queue"), headers={"Authorization": bad}) as r:
            assert r.status == 403


@pytest.mark.asyncio
async def test_my_queue_returns_only_callers_posts(server, store):
    pa = await _mk_post(store, USER_A)
    await _mk_post(store, USER_B)
    async with ClientSession() as s:
        async with s.get(server.url("/api/my/queue"),
                         headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 200
            body = await r.json()
    ids = [p["id"] for p in body["posts"]]
    assert ids == [pa]
    assert body["posts"][0]["destination_title"] == "Channel A"
    assert body["posts"][0]["kind"] == "text"
```

> Helpers verified against `core/state.py` and `tests/test_scheduler_recurring.py`: `create_scheduled_text_post(...) -> str`, `create_recurring_pattern(...) -> str`. Both return the id directly (not an object). `get_scheduled_post(id)` returns the `ScheduledPostRow`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_webapp_user_api.py -q`
Expected: FAIL (404/routes not found, or 403 for the "returns posts" test).

- [ ] **Step 3: Implement `_require_user` + `_post_to_json` + the route**

In `core/webapp.py`, add a module-level helper above `start_webapp_server`:

```python
async def _post_to_json(store: StateStore, row, tz_name: str) -> dict:
    from core.webapp_fmt import format_local  # or inline; see note
    title = await store.get_destination_title(row.chat_id) or str(row.chat_id)
    media = await store.get_post_media(row.id) if row.kind != "text" else []
    preview = (row.text or row.caption or "")[:120]
    return {
        "id": row.id,
        "destination_title": title,
        "scheduled_at_utc": row.scheduled_at_utc,
        "kind": row.kind,
        "media_count": len(media),
        "preview": preview,
    }
```

> Do NOT import a telegram formatter. Return the raw `scheduled_at_utc` epoch; the browser formats local time from it using the envelope `tz`. (No `webapp_fmt` module is needed — drop that import line; it is only a marker that formatting is client-side.)

Inside `start_webapp_server`, next to `_require_admin`:

```python
def _require_user(request: web.Request) -> dict | None:
    init_data = _extract_init_data(request)
    if not init_data:
        return None
    return validate_init_data(init_data, bot_token)

async def api_my_queue(request: web.Request) -> web.Response:
    user = _require_user(request)
    if user is None:
        return web.json_response({"error": "forbidden"}, status=403)
    user_id = int(user["id"])
    tz_name = await store.get_user_timezone(user_id) or "UTC"
    rows = await store.list_editable_pending_posts(user_id, limit=50, offset=0)
    posts = [await _post_to_json(store, r, tz_name) for r in rows]
    return web.json_response({"tz": tz_name, "lang": await store.get_user_language(user_id), "posts": posts})
```

Register: `app.router.add_get("/api/my/queue", api_my_queue)` near the other routes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_webapp_user_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/webapp.py tests/test_webapp_user_api.py
git commit -m "feat(webapp): _require_user gate + GET /api/my/queue"
```

---

## Task 2: `GET /api/my/recurring`

**Files:**
- Modify: `core/webapp.py`
- Test: `tests/test_webapp_user_api.py`

- [ ] **Step 1: Write failing test**

```python
# _mk_recurring is defined in Task 1's helper block (verified signature).

@pytest.mark.asyncio
async def test_my_recurring_returns_only_callers_patterns(server, store):
    pid = await _mk_recurring(store, USER_A)
    await _mk_recurring(store, USER_B)
    async with ClientSession() as s:
        async with s.get(server.url("/api/my/recurring"),
                         headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 200
            body = await r.json()
    ids = [p["id"] for p in body["patterns"]]
    assert ids == [pid]
    assert body["patterns"][0]["interval_type"] in {"daily", "weekly", "weekdays"}
    assert body["patterns"][0]["destination_title"] == "Channel A"


@pytest.mark.asyncio
async def test_my_recurring_requires_auth(server):
    async with ClientSession() as s:
        async with s.get(server.url("/api/my/recurring")) as r:
            assert r.status == 403
```

> A bare active pattern (no materialized instance) still appears in `list_user_recurring_summaries` with `next_scheduled_at_utc == null` (the next-instance columns are correlated subqueries; the base row only needs the destination JOIN to resolve). That is fine for this test.

- [ ] **Step 2: Run to verify fail** — `pytest tests/test_webapp_user_api.py -k recurring -q` → FAIL.

- [ ] **Step 3: Implement route**

```python
async def api_my_recurring(request: web.Request) -> web.Response:
    user = _require_user(request)
    if user is None:
        return web.json_response({"error": "forbidden"}, status=403)
    user_id = int(user["id"])
    tz_name = await store.get_user_timezone(user_id) or "UTC"
    summaries = await store.list_user_recurring_summaries(user_id, offset=0, limit=50)
    patterns = [{
        "id": s.pattern.id,
        "destination_title": s.destination_title,
        "interval_type": s.pattern.interval_type,
        "time_of_day_minutes": s.pattern.time_of_day_minutes,
        "next_scheduled_at_utc": s.next_scheduled_at_utc,
    } for s in summaries]
    return web.json_response({"tz": tz_name, "lang": await store.get_user_language(user_id), "patterns": patterns})
```

Register: `app.router.add_get("/api/my/recurring", api_my_recurring)`.

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_webapp_user_api.py -k recurring -q` → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(webapp): GET /api/my/recurring"`

---

## Task 3: `POST /api/my/post/{id}/reschedule`

**Files:** Modify `core/webapp.py`; test in `tests/test_webapp_user_api.py`.

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_reschedule_happy(server, store):
    pid = await _mk_post(store, USER_A)
    # +2 days at noon, format DD.MM.YYYY HH:MM in the user's tz (UTC here)
    from datetime import datetime, timezone, timedelta
    dt = datetime.now(timezone.utc) + timedelta(days=2)
    local = dt.strftime("%d.%m.%Y %H:%M")
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/my/post/{pid}/reschedule"),
                          json={"local_datetime": local},
                          headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 200
    row = await store.get_scheduled_post(pid)
    assert row.scheduled_at_utc > int(time.time()) + 86400


@pytest.mark.asyncio
async def test_reschedule_past_time_400(server, store):
    pid = await _mk_post(store, USER_A)
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/my/post/{pid}/reschedule"),
                          json={"local_datetime": "01.01.2000 10:00"},
                          headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 400


@pytest.mark.asyncio
async def test_reschedule_unparseable_400(server, store):
    pid = await _mk_post(store, USER_A)
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/my/post/{pid}/reschedule"),
                          json={"local_datetime": "not-a-date"},
                          headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 400


@pytest.mark.asyncio
async def test_reschedule_not_owned_404(server, store):
    pid = await _mk_post(store, USER_B)  # owned by B
    from datetime import datetime, timezone, timedelta
    local = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%d.%m.%Y %H:%M")
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/my/post/{pid}/reschedule"),
                          json={"local_datetime": local},
                          headers={"Authorization": _init_data(USER_A)}) as r:  # A acts
            assert r.status == 404
```

- [ ] **Step 2: Run to verify fail** → FAIL (404 for all; route missing).

- [ ] **Step 3: Implement route**

Add imports at top of `core/webapp.py`: `from core.utils import parse_local_datetime, validate_schedule_time`.

```python
async def api_my_reschedule(request: web.Request) -> web.Response:
    user = _require_user(request)
    if user is None:
        return web.json_response({"error": "forbidden"}, status=403)
    user_id = int(user["id"])
    post_id = request.match_info["id"]
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "bad_request"}, status=400)
    raw = str(payload.get("local_datetime") or "").strip()
    tz_name = await store.get_user_timezone(user_id) or "UTC"
    try:
        parsed = parse_local_datetime(raw, tz_name)
    except (ValueError, KeyError):
        return web.json_response({"error": "bad_datetime"}, status=400)
    check = validate_schedule_time(parsed.utc_epoch)
    if not check.is_valid:
        return web.json_response({"error": check.error_key}, status=400)
    ok = await store.update_editable_post_time(post_id, user_id, scheduled_at_utc=parsed.utc_epoch)
    if not ok:
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response({"ok": True, "scheduled_at_utc": parsed.utc_epoch})
```

Register: `app.router.add_post("/api/my/post/{id}/reschedule", api_my_reschedule)`.

- [ ] **Step 4: Run to verify pass** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(webapp): POST reschedule (server-side tz + validation)"`

---

## Task 4: `POST /api/my/post/{id}/cancel`

**Files:** Modify `core/webapp.py`; test in `tests/test_webapp_user_api.py`.

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_cancel_happy(server, store):
    pid = await _mk_post(store, USER_A)
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/my/post/{pid}/cancel"),
                          headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 200
    row = await store.get_scheduled_post(pid)
    assert row.status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_not_owned_404(server, store):
    pid = await _mk_post(store, USER_B)
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/my/post/{pid}/cancel"),
                          headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 404
```

- [ ] **Step 2: Run to verify fail** → FAIL.

- [ ] **Step 3: Implement route**

```python
async def api_my_cancel(request: web.Request) -> web.Response:
    user = _require_user(request)
    if user is None:
        return web.json_response({"error": "forbidden"}, status=403)
    ok = await store.cancel_post(int(user["id"]), request.match_info["id"])
    if not ok:
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response({"ok": True})
```

Register: `app.router.add_post("/api/my/post/{id}/cancel", api_my_cancel)`.

- [ ] **Step 4: Run to verify pass** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(webapp): POST cancel one-off post"`

---

## Task 5: `POST /api/my/recurring/{id}/cancel`

**Files:** Modify `core/webapp.py`; test in `tests/test_webapp_user_api.py`.

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_recurring_cancel_happy(server, store):
    pid = await _mk_recurring(store, USER_A)
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/my/recurring/{pid}/cancel"),
                          headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 200
    active = await store.list_user_recurring_summaries(USER_A, offset=0, limit=50)
    assert all(s.pattern.id != pid for s in active)  # no longer active


@pytest.mark.asyncio
async def test_recurring_cancel_not_owned_404(server, store):
    pid = await _mk_recurring(store, USER_B)
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/my/recurring/{pid}/cancel"),
                          headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 404
```

- [ ] **Step 2: Run to verify fail** → FAIL.

- [ ] **Step 3: Implement route**

```python
async def api_my_recurring_cancel(request: web.Request) -> web.Response:
    user = _require_user(request)
    if user is None:
        return web.json_response({"error": "forbidden"}, status=403)
    ok = await store.cancel_recurring_pattern(int(user["id"]), request.match_info["id"])
    if not ok:
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response({"ok": True})
```

Register: `app.router.add_post("/api/my/recurring/{id}/cancel", api_my_recurring_cancel)`.

- [ ] **Step 4: Run to verify pass** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(webapp): POST cancel recurring series"`

---

## Task 6: `GET /app` + `core/webapp_static/queue.html`

**Files:**
- Modify: `core/webapp.py` (add `/app` route + `app_index` handler)
- Create: `core/webapp_static/queue.html`
- Test: `tests/test_webapp_user_api.py`

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_app_serves_html(server):
    async with ClientSession() as s:
        async with s.get(server.url("/app")) as r:
            assert r.status == 200
            assert r.content_type == "text/html"
            body = await r.text()
    assert "<html" in body.lower()
```

- [ ] **Step 2: Run to verify fail** → FAIL (404).

- [ ] **Step 3a: Add the route**

```python
async def app_index(_request: web.Request) -> web.Response:
    html_path = _STATIC_DIR / "queue.html"
    return web.Response(text=html_path.read_text(encoding="utf-8"), content_type="text/html")
```

Register: `app.router.add_get("/app", app_index)`.

- [ ] **Step 3b: Create `core/webapp_static/queue.html`**

Build a single self-contained file. **Copy the entire `<style>` block and theme-variable setup from `core/webapp_static/admin.html`** so light/dark theme and typography match. Then implement this body + script (adapt class names to admin.html's):

Structure:
- `<h1>` title (e.g. "Мои посты").
- Section "Запланированные" containing `<div id="queue-list">`.
- Section "Повторяющиеся" containing `<div id="recurring-list">`.
- A hidden reschedule dialog/inline form (a `<input type="text" placeholder="ДД.ММ.ГГГГ ЧЧ:ММ">` + confirm/cancel buttons), or use two `<input type="date">` + `<input type="time">` and compose the `DD.MM.YYYY HH:MM` string in JS.

Core JS:

```html
<script>
const tg = window.Telegram?.WebApp;
const initData = tg?.initData || "";
tg?.ready?.();
const AUTH = { "Authorization": "tma " + initData };
let TZ = "UTC", LANG = "ru";

const INTERVAL_LABELS = {
  ru: { daily: "Ежедневно", weekly: "Еженедельно", weekdays: "По будням" },
  en: { daily: "Daily", weekly: "Weekly", weekdays: "Weekdays" },
};
function intervalLabel(t) {
  return (INTERVAL_LABELS[LANG] || INTERVAL_LABELS.en)[t] || t;
}
function fmtLocal(epoch) {
  if (!epoch) return "—";
  // Render in the user's tz using Intl
  return new Intl.DateTimeFormat(LANG === "ru" ? "ru-RU" : "en-GB", {
    timeZone: TZ, day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  }).format(new Date(epoch * 1000));
}
function pad(n){return String(n).padStart(2,"0");}
function composeLocal(dateStr, timeStr) { // "2026-08-02","14:30" -> "02.08.2026 14:30"
  const [y,m,d] = dateStr.split("-");
  return `${d}.${m}.${y} ${timeStr}`;
}

async function api(path, opts) {
  const r = await fetch(path, { headers: { ...AUTH, ...(opts?.headers||{}) }, ...opts });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

async function loadAll() {
  const demo = !initData;
  if (demo) { renderDemo(); return; }
  const [q, rec] = await Promise.all([api("/api/my/queue"), api("/api/my/recurring")]);
  TZ = q.tz || TZ; LANG = q.lang || LANG;
  renderQueue(q.posts); renderRecurring(rec.patterns);
}

function renderQueue(posts) {
  const el = document.getElementById("queue-list");
  el.innerHTML = posts.length ? "" : "<p class='empty'>Нет запланированных постов</p>";
  for (const p of posts) {
    const card = document.createElement("div"); card.className = "card";
    const kind = p.kind === "text" ? "Текст" : `Медиа × ${p.media_count}`;
    card.innerHTML = `
      <div class="row"><b>${escapeHtml(p.destination_title)}</b><span>${fmtLocal(p.scheduled_at_utc)}</span></div>
      <div class="meta">${kind} · ${escapeHtml(p.preview)}</div>
      <div class="actions">
        <button data-act="resched" data-id="${p.id}">Перенести</button>
        <button data-act="cancel" data-id="${p.id}" class="danger">Отменить</button>
      </div>`;
    el.appendChild(card);
  }
}

function renderRecurring(patterns) {
  const el = document.getElementById("recurring-list");
  el.innerHTML = patterns.length ? "" : "<p class='empty'>Нет повторяющихся</p>";
  for (const p of patterns) {
    const tod = pad(Math.floor(p.time_of_day_minutes/60)) + ":" + pad(p.time_of_day_minutes%60);
    const card = document.createElement("div"); card.className = "card";
    card.innerHTML = `
      <div class="row"><b>${escapeHtml(p.destination_title)}</b><span>${intervalLabel(p.interval_type)} · ${tod}</span></div>
      <div class="meta">Следующий: ${fmtLocal(p.next_scheduled_at_utc)}</div>
      <div class="actions">
        <button data-act="rec-cancel" data-id="${p.id}" class="danger">Отменить серию</button>
      </div>`;
    el.appendChild(card);
  }
}

function escapeHtml(s){return (s||"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}

document.addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]"); if (!btn) return;
  const id = btn.dataset.id, act = btn.dataset.act;
  try {
    if (act === "cancel") {
      if (!confirm("Отменить пост?")) return;
      await api(`/api/my/post/${id}/cancel`, { method: "POST" });
    } else if (act === "rec-cancel") {
      if (!confirm("Отменить всю серию?")) return;
      await api(`/api/my/recurring/${id}/cancel`, { method: "POST" });
    } else if (act === "resched") {
      const date = prompt("Новая дата (ГГГГ-ММ-ДД):"); if (!date) return;
      const tm = prompt("Время (ЧЧ:ММ):"); if (!tm) return;
      await api(`/api/my/post/${id}/reschedule`, {
        method: "POST", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ local_datetime: composeLocal(date, tm) }),
      });
    }
    await loadAll();
  } catch (err) { alert("Ошибка: " + err.message); }
});

function renderDemo() {
  TZ = "UTC"; LANG = "ru";
  renderQueue([{id:"demo1",destination_title:"Demo Channel",scheduled_at_utc:Math.floor(Date.now()/1000)+7200,kind:"text",media_count:0,preview:"Пример поста"}]);
  renderRecurring([{id:"demoR",destination_title:"Demo Channel",interval_type:"daily",time_of_day_minutes:540,next_scheduled_at_utc:Math.floor(Date.now()/1000)+86400}]);
}
loadAll();
</script>
```

> The `prompt()`-based reschedule is the minimal v1. If admin.html already provides a nicer modal component, reuse it instead — but do NOT reimplement server-side validation in JS; the server owns validation (Task 3). Keep the file self-contained (no external CDN/fonts) exactly like admin.html.

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_webapp_user_api.py::test_app_serves_html -q` → PASS.

- [ ] **Step 5: Manual smoke (optional now, required in Task 8)** — open `server.url("/app")` in a browser; confirm demo data renders (no initData path). Then commit.

```bash
git add core/webapp.py core/webapp_static/queue.html
git commit -m "feat(webapp): GET /app serving user queue Mini App"
```

---

## Task 7: Bot entry points (`/app` command + `/queue` button)

**Files:**
- Modify: `telegram/i18n.py` (en block ~line 323, ru block ~line 694 — next to `admin_open_btn`)
- Create: `telegram/user_app.py`
- Modify: `telegram/router.py`, `telegram/handlers/queue.py`, `main.py`
- Test: `tests/test_user_app_entry.py` (create)

- [ ] **Step 1: Add i18n strings**

In `telegram/i18n.py`, add to the `"en"` dict (near `admin_open_btn`):
```python
"app_intro": "📱 Your posts — open the app to view, reschedule, or cancel.",
"app_open_btn": "Open my posts",
"app_not_configured": "The app is not configured yet.",
```
And to `"ru"`:
```python
"app_intro": "📱 Ваши посты — откройте приложение, чтобы посмотреть, перенести или отменить.",
"app_open_btn": "Открыть мои посты",
"app_not_configured": "Приложение пока не настроено.",
```

- [ ] **Step 2: Write failing test**

```python
# tests/test_user_app_entry.py
from __future__ import annotations
import pytest
from telegram.user_app import build_user_app_router

def test_user_app_router_builds_with_url():
    r = build_user_app_router(store=object(), webapp_url="https://example.org")
    assert r is not None

def test_user_app_router_builds_without_url():
    r = build_user_app_router(store=object(), webapp_url=None)
    assert r is not None
```

Run: `.venv/bin/python -m pytest tests/test_user_app_entry.py -q` → FAIL (import error).

- [ ] **Step 3a: Create `telegram/user_app.py`** (mirror `telegram/admin.py`)

```python
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from core.state import StateStore
from telegram.i18n import tr


def build_user_app_router(*, store: StateStore, webapp_url: str | None) -> Router:
    router = Router(name="user_app")

    @router.message(Command("app"))
    async def cmd_app(message: Message) -> None:
        user = message.from_user
        if user is None:
            return
        await store.ensure_user(user.id)
        lang = await store.get_user_language(user.id)
        if not webapp_url:
            await message.answer(tr(lang, "app_not_configured"))
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=tr(lang, "app_open_btn"),
                                 web_app=WebAppInfo(url=f"{webapp_url.rstrip('/')}/app"))
        ]])
        await message.answer(tr(lang, "app_intro"), reply_markup=kb)

    return router
```

- [ ] **Step 3b: Thread `webapp_url` to the queue router**

In `telegram/router.py`: change signature to `def build_router(store: StateStore, *, webapp_url: str | None = None) -> Router:` and the queue include to `router.include_router(queue.build_router(store, webapp_url=webapp_url))`. Leave the other feature routers unchanged.

In `telegram/handlers/queue.py`:
- Change `def build_router(store: StateStore) -> Router:` → `def build_router(store: StateStore, *, webapp_url: str | None = None) -> Router:`.
- Import `WebAppInfo` (already imports from `aiogram.types`; add it to the tuple).
- In `_render_queue_page`, after building `reply_markup = kb._queue_paged_kb(...)`, append a `web_app` button row **only when `webapp_url`** is set. Since `_render_queue_page` is a module-level function (not inside `build_router`), thread `webapp_url` into it as a keyword arg from both call sites (`cmd_queue` and `cb_queue_page`), OR read it via a closure. Simplest: add `webapp_url: str | None = None` param to `_render_queue_page` and pass it from the handlers (which capture `webapp_url` from `build_router`'s scope). Append:

```python
if webapp_url and reply_markup is not None:
    reply_markup.inline_keyboard.append([
        InlineKeyboardButton(text=tr(lang, "app_open_btn"),
                             web_app=WebAppInfo(url=f"{webapp_url.rstrip('/')}/app"))
    ])
```

> Note: `web_app` inline buttons require an HTTPS url (satisfied by `WEBAPP_URL`). In local/HTTP dev the button may not open — acceptable; `/queue` text list remains the fallback.

- [ ] **Step 3c: Wire `main.py`**

- Change `dp.include_router(build_router(store=store))` → `dp.include_router(build_router(store=store, webapp_url=cfg.webapp_url))`.
- Add after the admin router include: `dp.include_router(build_user_app_router(store=store, webapp_url=cfg.webapp_url))` and import it: `from telegram.user_app import build_user_app_router`.

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest tests/test_user_app_entry.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add telegram/i18n.py telegram/user_app.py telegram/router.py telegram/handlers/queue.py main.py tests/test_user_app_entry.py
git commit -m "feat(bot): /app command + web_app button on /queue"
```

---

## Task 8: Full-suite verification + memory + integrate

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all green (existing 276 + new). Fix any regression (esp. `telegram/router.py` signature change — grep for other `build_router(` callers in tests).

- [ ] **Step 2: pyflakes clean**

Run: `.venv/bin/python -m pyflakes core/webapp.py telegram/user_app.py telegram/handlers/queue.py telegram/router.py main.py`
Expected: no output.

- [ ] **Step 3: Manual smoke (per superpowers:verification-before-completion)**

Start the server locally (or open `queue.html` via the `/app` route with no initData) and confirm: demo data renders; theme matches admin; buttons present. If a tunnel/`WEBAPP_URL` is available, open from two Telegram accounts and confirm each sees only their own queue, reschedule respects tz, cancel works, recurring cancel stops future instances.

- [ ] **Step 4: Update project memory**

Update `admin_miniapp.md` memory: mark user Mini App SHIPPED, note routes (`/api/my/queue`, `/api/my/recurring`, reschedule/cancel/recurring-cancel), `_require_user` gate, `queue.html`, `/app` + `/queue` button, client-side interval labels (core-layer boundary). Update the MEMORY.md hook line if needed.

- [ ] **Step 5: Integrate** — invoke `superpowers:finishing-a-development-branch` to merge `feature/user-mini-app-queue` → `main` when green.

---

## Verification Checklist (spec → test mapping)

- `_require_user` accepts valid initData, rejects missing/bad-signature → `test_my_queue_requires_auth`, `test_my_queue_bad_signature_forbidden`.
- Queue/recurring return only caller's rows → `test_my_queue_returns_only_callers_posts`, `test_my_recurring_returns_only_callers_patterns`.
- Cross-user isolation → `test_reschedule_not_owned_404`, `test_cancel_not_owned_404`, `test_recurring_cancel_not_owned_404`.
- Reschedule validation (past/unparseable) → `test_reschedule_past_time_400`, `test_reschedule_unparseable_400`; happy → `test_reschedule_happy`.
- Cancel + recurring cancel happy → `test_cancel_happy`, `test_recurring_cancel_happy`.
- `/app` serves HTML → `test_app_serves_html`.
- Entry points build with/without `WEBAPP_URL` → `tests/test_user_app_entry.py`.
