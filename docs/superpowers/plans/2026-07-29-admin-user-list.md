# Admin User List + Per-User Stats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the admin Mini App a list of every user (with their numeric Telegram id and a name label) that drills into per-user stats, and start capturing username/first_name so labels populate.

**Architecture:** Additive SQLite columns on `users` captured in the existing `ensure_user` upsert via `COALESCE`; two new read methods on `StateStore` (`list_users`, enriched `get_user_profile`); one new admin-guarded aiohttp route `GET /api/users`; a new "all users" card plus expanded detail card in the single self-contained `admin.html`.

**Tech Stack:** Python 3.10+, aiosqlite, aiogram 3.25, aiohttp, vanilla-JS Mini App. Tests: pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-07-29-admin-user-list-design.md`

---

## File Structure

- **Modify** `core/state.py` — add columns in `migrate()`; extend `ensure_user`; add `list_users`; enrich `get_user_profile`.
- **Modify** `telegram/router.py` — pass `username`/`first_name` into `ensure_user` at `cmd_start`.
- **Modify** `core/webapp.py` — add `GET /api/users` route.
- **Modify** `core/webapp_static/admin.html` — "all users" card, expanded detail card, demo data.
- **Create** `tests/test_state_users.py` — `ensure_user` capture, `list_users`, `get_user_profile`.
- **Modify** `tests/test_webapp_server.py` — coverage for `/api/users`.

> **Gotcha (from prior work):** any test that builds a `StateStore` MUST close the connection in teardown (`await conn.close()`), or pytest-asyncio hangs. The `store` fixtures in the referenced test files already do this — copy that pattern. A post can only be marked sent after `claim_post_for_sending` (pending→sending→sent).

---

## Task 1: Capture username/first_name in the users table

**Files:**
- Modify: `core/state.py` — `migrate()` (near the existing `PRAGMA table_info(users)` guard, ~line 343) and `ensure_user` (~line 377)
- Test: `tests/test_state_users.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_state_users.py`:

```python
from __future__ import annotations

import pytest
import pytest_asyncio

from core.db import open_db
from core.state import StateStore


@pytest_asyncio.fixture
async def store() -> StateStore:
    conn = await open_db(":memory:")
    state = StateStore(conn)
    await state.migrate()
    yield state
    await conn.close()


@pytest.mark.asyncio
async def test_ensure_user_captures_name(store: StateStore) -> None:
    await store.ensure_user(1, username="alice", first_name="Alice")
    prof = await store.get_user_profile(1)
    assert prof["username"] == "alice"
    assert prof["first_name"] == "Alice"


@pytest.mark.asyncio
async def test_ensure_user_coalesce_preserves_name(store: StateStore) -> None:
    await store.ensure_user(1, username="alice", first_name="Alice")
    # A later interaction with no name (e.g. a callback) must not wipe it.
    await store.ensure_user(1)
    prof = await store.get_user_profile(1)
    assert prof["username"] == "alice"
    assert prof["first_name"] == "Alice"


@pytest.mark.asyncio
async def test_ensure_user_updates_name(store: StateStore) -> None:
    await store.ensure_user(1, username="alice", first_name="Alice")
    await store.ensure_user(1, username="alice2", first_name="Alicia")
    prof = await store.get_user_profile(1)
    assert prof["username"] == "alice2"
    assert prof["first_name"] == "Alicia"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_state_users.py -v`
Expected: FAIL — `ensure_user()` got an unexpected keyword argument `username` (or KeyError on `username` in profile).

- [ ] **Step 3: Add the guarded columns in `migrate()`**

In `core/state.py`, right after the existing `language` guard block:

```python
        if "username" not in user_column_names:
            await self._conn.execute("ALTER TABLE users ADD COLUMN username TEXT NULL")
        if "first_name" not in user_column_names:
            await self._conn.execute("ALTER TABLE users ADD COLUMN first_name TEXT NULL")
```

(`user_column_names` is already computed one line above from `PRAGMA table_info(users)`. Do NOT emit an unguarded `ALTER` — it fails on re-run.)

- [ ] **Step 4: Extend `ensure_user`**

Replace the existing `ensure_user` body with:

```python
    async def ensure_user(
        self,
        user_id: int,
        username: str | None = None,
        first_name: str | None = None,
    ) -> None:
        now = int(time.time())
        await self._conn.execute(
            """
            INSERT INTO users(user_id, timezone, language, username, first_name, created_at, updated_at)
            VALUES(?, NULL, NULL, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                updated_at = excluded.updated_at,
                username   = COALESCE(excluded.username, users.username),
                first_name = COALESCE(excluded.first_name, users.first_name)
            """,
            (user_id, username, first_name, now, now),
        )
        await self._conn.commit()
```

- [ ] **Step 5: Add `username`/`first_name` to `get_user_profile`**

In `get_user_profile`, change the SELECT and returned dict:

```python
        row = await self._execute_fetchone(
            "SELECT user_id, timezone, language, username, first_name, created_at FROM users WHERE user_id=?",
            (user_id,),
        )
```

and add to the returned dict:

```python
            "username": row["username"],
            "first_name": row["first_name"],
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_state_users.py -v`
Expected: PASS (3 passed)

- [ ] **Step 7: Commit**

```bash
git add core/state.py tests/test_state_users.py
git commit -m "feat(state): capture username/first_name on users via ensure_user"
```

---

## Task 2: `list_users` read method

**Files:**
- Modify: `core/state.py` (add method near `top_active_users`, ~line 544)
- Test: `tests/test_state_users.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_state_users.py`:

```python
@pytest.mark.asyncio
async def test_list_users_orders_by_last_active(store: StateStore) -> None:
    import time as _t
    await store.ensure_user(1, username="a", first_name="A")
    await store.ensure_user(2, username="b", first_name="B")
    # Force distinct updated_at so ordering is deterministic.
    await store._conn.execute("UPDATE users SET updated_at=100 WHERE user_id=1")
    await store._conn.execute("UPDATE users SET updated_at=200 WHERE user_id=2")
    await store._conn.commit()

    users = await store.list_users()
    assert [u["user_id"] for u in users] == [2, 1]  # most recently active first
    assert users[0]["username"] == "b"
    assert "posts" in users[0] and "channels" in users[0]
    assert users[0]["last_active"] == 200


@pytest.mark.asyncio
async def test_list_users_limit_offset(store: StateStore) -> None:
    for uid in (1, 2, 3):
        await store.ensure_user(uid)
    page = await store.list_users(limit=2, offset=0)
    assert len(page) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_state_users.py -k list_users -v`
Expected: FAIL — `'StateStore' object has no attribute 'list_users'`

- [ ] **Step 3: Implement `list_users`**

Add to `core/state.py`:

```python
    async def list_users(self, limit: int = 100, offset: int = 0) -> list[dict]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT
                u.user_id,
                u.username,
                u.first_name,
                u.language,
                u.created_at,
                u.updated_at AS last_active,
                (SELECT COUNT(1) FROM scheduled_posts sp WHERE sp.user_id = u.user_id) AS posts,
                (SELECT COUNT(1) FROM user_destinations ud WHERE ud.user_id = u.user_id) AS channels
            FROM users u
            ORDER BY u.updated_at DESC, u.user_id ASC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [
            {
                "user_id": int(r["user_id"]),
                "username": r["username"],
                "first_name": r["first_name"],
                "language": r["language"],
                "created_at": int(r["created_at"]),
                "last_active": int(r["last_active"]),
                "posts": int(r["posts"]),
                "channels": int(r["channels"]),
            }
            for r in rows
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_state_users.py -k list_users -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add core/state.py tests/test_state_users.py
git commit -m "feat(state): add list_users for admin panel"
```

---

## Task 3: Per-status post breakdown in `get_user_profile`

**Files:**
- Modify: `core/state.py` — `get_user_profile` (~line 546)
- Test: `tests/test_state_users.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_state_users.py`:

```python
@pytest.mark.asyncio
async def test_get_user_profile_status_breakdown(store: StateStore) -> None:
    await store.ensure_user(1, username="a", first_name="A")
    prof = await store.get_user_profile(1)
    # All five statuses present and summing to total posts.
    assert set(prof["posts_by_status"]) == {
        "pending", "sending", "sent", "failed", "cancelled",
    }
    assert sum(prof["posts_by_status"].values()) == prof["posts"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_state_users.py -k status_breakdown -v`
Expected: FAIL — KeyError `posts_by_status`

- [ ] **Step 3: Implement the breakdown**

In `get_user_profile`, after computing `posts`, add:

```python
        status_rows = await self._conn.execute_fetchall(
            "SELECT status, COUNT(1) AS cnt FROM scheduled_posts WHERE user_id=? GROUP BY status",
            (user_id,),
        )
        posts_by_status = {"pending": 0, "sending": 0, "sent": 0, "failed": 0, "cancelled": 0}
        for r in status_rows:
            posts_by_status[str(r["status"])] = int(r["cnt"])
```

and add to the returned dict:

```python
            "posts_by_status": posts_by_status,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_state_users.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add core/state.py tests/test_state_users.py
git commit -m "feat(state): add posts_by_status breakdown to get_user_profile"
```

---

## Task 4: Wire name capture at the /start handler

**Files:**
- Modify: `telegram/router.py` — `cmd_start` (~line 2073)

- [ ] **Step 1: Update the call**

Replace:

```python
        await store.ensure_user(message.from_user.id)
```

inside `cmd_start` with:

```python
        await store.ensure_user(
            message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
```

> The bare string `await store.ensure_user(message.from_user.id)` appears ~20 times in `router.py`. Target ONLY the `cmd_start` occurrence (~line 2073) — do NOT use `replace_all`.

Leave the other `ensure_user(...)` call sites unchanged — the `COALESCE` upsert keeps their stored name intact; `/start` is the reliable first-contact point that seeds it.

- [ ] **Step 2: Run the full suite to confirm no regression**

Run: `pytest -q`
Expected: all existing tests pass (the new keyword args are optional, so router tests are unaffected).

- [ ] **Step 3: Commit**

```bash
git add telegram/router.py
git commit -m "feat(router): pass username/first_name to ensure_user on /start"
```

---

## Task 5: `GET /api/users` admin endpoint

**Files:**
- Modify: `core/webapp.py` — inside `start_webapp_server` (routes registered ~line 158-161)
- Test: `tests/test_webapp_server.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_webapp_server.py`, add these. The `server` fixture yields a `WebappServer` dataclass — use `server.url(path)` and open your own `ClientSession` (this mirrors the five existing tests directly above the insertion point). The `store` fixture already seeds `ADMIN_ID` (and `_init_data` carries `first_name:"Ann"`), so an admin row exists and `ADMIN_ID in ids` holds.

```python
@pytest.mark.asyncio
async def test_api_users_forbidden_without_admin(server, store) -> None:
    async with ClientSession() as session:
        async with session.get(
            server.url("/api/users"),
            headers={"Authorization": _init_data(NON_ADMIN_ID)},
        ) as resp:
            assert resp.status == 403


@pytest.mark.asyncio
async def test_api_users_returns_list_for_admin(server, store) -> None:
    async with ClientSession() as session:
        async with session.get(
            server.url("/api/users"),
            headers={"Authorization": _init_data(ADMIN_ID)},
        ) as resp:
            assert resp.status == 200
            body = await resp.json()
            assert "users" in body
            ids = [u["user_id"] for u in body["users"]]
            assert ADMIN_ID in ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_webapp_server.py -k api_users -v`
Expected: FAIL — 404 (route not registered)

- [ ] **Step 3: Add the handler and route**

In `core/webapp.py`, alongside `api_user` (before `app = web.Application()`):

```python
    async def api_users(request: web.Request) -> web.Response:
        if _require_admin(request) is None:
            return web.json_response({"error": "forbidden"}, status=403)
        return web.json_response({"users": await store.list_users()})
```

and register it next to the other routes:

```python
    app.router.add_get("/api/users", api_users)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_webapp_server.py -k api_users -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add core/webapp.py tests/test_webapp_server.py
git commit -m "feat(webapp): add admin GET /api/users endpoint"
```

---

## Task 6: "All users" card + expanded detail in admin.html

**Files:**
- Modify: `core/webapp_static/admin.html` (markup ~line 176-193; JS `showProfile` ~line 383, `load()` ~line 419, demo data ~line 401)

This is a static, self-contained HTML file with no unit tests. Verify by serving the page and opening it standalone (demo mode) and, if possible, inside Telegram.

- [ ] **Step 1: Add the "all users" card markup**

Insert a new `<section>` between the "Топ активных" card (ends line ~176) and the "Найти пользователя" card (line ~178):

```html
  <section class="card">
    <div class="block-head"><div class="eyebrow">Все пользователи</div><div class="meta" id="usersListMeta"></div></div>
    <div id="usersList"></div>
  </section>
```

- [ ] **Step 2: Add a created-at field and status breakdown to the detail card**

Extend the `.grid` inside `#findCard` (after the `ucTz` field, line ~190):

```html
        <div class="f"><div class="k">Регистрация</div><div class="val num" id="ucCreated" style="font-size:14px">—</div></div>
        <div class="f"><div class="k">Статусы</div><div class="val num" id="ucStatuses" style="font-size:13px">—</div></div>
```

- [ ] **Step 3: Render the users list and make rows clickable**

In the `<script>`, add a render helper and fetch. Reuse the existing `api()`, `esc()`, `$()`, `nf`, and `findUser`/`showProfile` machinery. Add:

```js
  function userLabel(u){
    if(u.username){ return "@"+esc(u.username); }
    if(u.first_name){ return esc(u.first_name); }
    return "ID "+u.user_id;
  }
  function renderUsers(list){
    var el=$("usersList");
    if(!list || !list.length){ el.innerHTML='<div class="u-msg show">Пока нет пользователей</div>'; return; }
    $("usersListMeta").textContent = list.length + "";
    el.innerHTML = list.map(function(u){
      return '<div class="urow" data-id="'+u.user_id+'">'
        + '<div class="uinfo"><div class="ulabel">'+userLabel(u)+'</div>'
        + '<div class="uid"><span class="uidnum">'+u.user_id+'</span>'
        + '<button class="ucopy" data-copy="'+u.user_id+'" title="Скопировать ID" aria-label="Скопировать ID">⧉</button></div></div>'
        + '<div class="ustat">'+nf.format(u.posts)+' · '+nf.format(u.channels)+'</div></div>';
    }).join("");
    el.querySelectorAll(".urow").forEach(function(row){
      row.addEventListener("click", function(e){
        if(e.target.classList.contains("ucopy")) return;
        $("findInput").value = row.getAttribute("data-id");
        findUser();
        $("findCard").scrollIntoView({behavior:"smooth", block:"nearest"});
      });
    });
    el.querySelectorAll(".ucopy").forEach(function(btn){
      btn.addEventListener("click", function(e){
        e.stopPropagation();
        var v = btn.getAttribute("data-copy");
        if(navigator.clipboard){ navigator.clipboard.writeText(v); }
        btn.textContent = "✓"; setTimeout(function(){ btn.textContent="⧉"; }, 900);
      });
    });
  }
```

Add minimal CSS in the existing `<style>` block (match the file's visual language — reuse existing color CSS vars):

```css
  .urow{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 0;border-bottom:1px solid var(--separator);cursor:pointer}
  .urow:last-child{border-bottom:none}
  .ulabel{font-weight:600}
  .uid{display:flex;align-items:center;gap:6px;margin-top:2px}
  .uidnum{font-family:ui-monospace,Menlo,monospace;font-size:12px;opacity:.7}
  .ucopy{background:none;border:none;cursor:pointer;font-size:13px;opacity:.6;padding:0}
  .ustat{font-variant-numeric:tabular-nums;opacity:.8;font-size:13px;white-space:nowrap}
```

> `admin.html` uses `var(--separator)` for borders (confirmed — there is no `--line`). If the var name differs in your copy, match whatever the other cards already use.

- [ ] **Step 4: Extend `showProfile` for the new fields**

Update `showProfile`:

```js
  function showProfile(p){
    $("ucChannels").textContent = nf.format(p.channels);
    $("ucPosts").textContent = nf.format(p.posts);
    $("ucLang").textContent = p.language || "—";
    $("ucTz").textContent = p.timezone || "—";
    $("ucCreated").textContent = p.created_at
      ? new Date(p.created_at*1000).toISOString().slice(0,10) : "—";
    if(p.posts_by_status){
      var s = p.posts_by_status;
      $("ucStatuses").textContent =
        "⏳"+ (s.pending+s.sending) +" ✓"+ s.sent +" ✗"+ s.failed +" ⊘"+ s.cancelled;
    } else {
      $("ucStatuses").textContent = "—";
    }
    $("findCard").classList.add("show");
  }
```

- [ ] **Step 5: Load the list (live) and add demo data**

In `load()`, after `api("/api/stats").then(render)...`, also fetch users:

```js
  function load(){
    if(!initData){ render(demo()); renderUsers(demoUsers()); return; }
    api("/api/stats").then(render).catch(function(){
      banner("Нет доступа. Проверьте, что вы в списке администраторов.");
    });
    api("/api/users").then(function(d){ renderUsers(d.users); }).catch(function(){});
  }
```

Add a `demoUsers()` helper next to `demo()`:

```js
  function demoUsers(){
    return [
      {user_id:81724, username:"alice", first_name:"Alice", posts:143, channels:4, last_active:0, created_at:1700000000},
      {user_id:24019, username:null, first_name:"Bob", posts:118, channels:2, last_active:0, created_at:1700500000},
      {user_id:550212, username:null, first_name:null, posts:97, channels:1, last_active:0, created_at:1701000000}
    ];
  }
```

Also update the standalone-preview branch of `findUser()` (the `if(!initData)` block, line ~373) to include the new fields so the demo detail card renders them:

```js
      showProfile({user_id:id, channels:3, posts:14, language:"ru", timezone:"Europe/Moscow",
        created_at:1700000000, posts_by_status:{pending:2,sending:0,sent:10,failed:1,cancelled:1}});
```

- [ ] **Step 6: Verify the page renders**

Run the existing page test and serve locally:

Run: `pytest tests/test_webapp_page.py -v`
Expected: PASS (page still serves).

Then open the page standalone (demo mode) to eyeball the users list, copy button, row-click → detail with registration date + status line. Reference @superpowers:verification-before-completion — confirm visually before claiming done.

- [ ] **Step 7: Commit**

```bash
git add core/webapp_static/admin.html
git commit -m "feat(admin-ui): all-users list with ids + expanded per-user detail"
```

---

## Final verification

- [ ] Run the whole suite: `pytest -q` — expected: all green (including new `tests/test_state_users.py` and the `/api/users` tests).
- [ ] Manual smoke: open `/admin` in Telegram → confirm the "Все пользователи" card lists real ids, copy works, and clicking a row opens the detail with registration date + status breakdown.
- [ ] Update memory `admin_miniapp.md` with the new `list_users`, `/api/users`, and captured name columns.
