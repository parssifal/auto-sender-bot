from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest
import pytest_asyncio
from aiohttp import ClientSession

from core.db import open_db
from core.state import StateStore
from core.webapp import start_webapp_server

TOKEN = "123456:test-token"
USER_A = 111
USER_B = 222
CHAT_A = -3001


def _init_data(user_id: int, *, token: str = TOKEN, auth_date: int | None = None) -> str:
    user = {"id": user_id, "first_name": "U"}
    fields = {
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
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
    at = int(time.time()) + when_offset
    return await store.create_scheduled_text_post(user_id, CHAT_A, at, "hello", None)


async def _mk_recurring(store: StateStore, user_id: int) -> str:
    at = int(time.time()) + 3600
    return await store.create_recurring_pattern(
        user_id=user_id, chat_id=CHAT_A, interval_type="daily",
        time_of_day_minutes=540, timezone="UTC", start_at_utc=at,
    )


# --- Task 1: queue ---


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
async def test_my_queue_rejects_initdata_older_than_600s(server):
    # The caller enforces a 600s effective TTL on initData.
    stale = _init_data(USER_A, auth_date=int(time.time()) - 700)
    async with ClientSession() as s:
        async with s.get(server.url("/api/my/queue"),
                         headers={"Authorization": stale}) as r:
            assert r.status == 403


@pytest.mark.asyncio
async def test_my_queue_accepts_initdata_within_600s(server):
    fresh = _init_data(USER_A, auth_date=int(time.time()) - 100)
    async with ClientSession() as s:
        async with s.get(server.url("/api/my/queue"),
                         headers={"Authorization": fresh}) as r:
            assert r.status == 200


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


@pytest.mark.asyncio
async def test_my_queue_signals_truncation(server, store):
    # 51 posts > the 50 limit: the payload must flag that more exist.
    now = int(time.time())
    for i in range(51):
        await store.create_scheduled_text_post(USER_A, CHAT_A, now + 3600 + i, "hi", None)
    async with ClientSession() as s:
        async with s.get(server.url("/api/my/queue"),
                         headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 200
            body = await r.json()
    assert len(body["posts"]) == 50
    assert body["has_more"] is True


@pytest.mark.asyncio
async def test_my_queue_no_truncation_flag_when_under_limit(server, store):
    await _mk_post(store, USER_A)
    async with ClientSession() as s:
        async with s.get(server.url("/api/my/queue"),
                         headers={"Authorization": _init_data(USER_A)}) as r:
            body = await r.json()
    assert body["has_more"] is False


# --- Task 2: recurring ---


@pytest.mark.asyncio
async def test_my_recurring_requires_auth(server):
    async with ClientSession() as s:
        async with s.get(server.url("/api/my/recurring")) as r:
            assert r.status == 403


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


# --- Task 3: reschedule ---


@pytest.mark.asyncio
async def test_reschedule_happy(server, store):
    pid = await _mk_post(store, USER_A)
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
async def test_reschedule_dst_gap_400(server, store):
    # T-07 (Mini App surface): 02:30 on 2026-03-29 does not exist in Europe/Berlin.
    await store.set_user_timezone(USER_A, "Europe/Berlin")
    pid = await _mk_post(store, USER_A)
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/my/post/{pid}/reschedule"),
                          json={"local_datetime": "29.03.2026 02:30"},
                          headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 400
            body = await r.json()
            assert body["error"] == "datetime_dst_gap"


@pytest.mark.asyncio
async def test_reschedule_not_owned_404(server, store):
    pid = await _mk_post(store, USER_B)
    local = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%d.%m.%Y %H:%M")
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/my/post/{pid}/reschedule"),
                          json={"local_datetime": local},
                          headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 404


# --- Task 4: cancel post ---


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


# --- Task 5: cancel recurring ---


@pytest.mark.asyncio
async def test_recurring_cancel_happy(server, store):
    pid = await _mk_recurring(store, USER_A)
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/my/recurring/{pid}/cancel"),
                          headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 200
    active = await store.list_user_recurring_summaries(USER_A, offset=0, limit=50)
    assert all(s.pattern.id != pid for s in active)


@pytest.mark.asyncio
async def test_recurring_cancel_not_owned_404(server, store):
    pid = await _mk_recurring(store, USER_B)
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/my/recurring/{pid}/cancel"),
                          headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 404


# --- Task 6: /app serves html ---


@pytest.mark.asyncio
async def test_app_serves_html(server):
    async with ClientSession() as s:
        async with s.get(server.url("/app")) as r:
            assert r.status == 200
            assert r.content_type == "text/html"
            body = await r.text()
    assert "<html" in body.lower()


@pytest.mark.asyncio
async def test_my_queue_reports_admin_flag(store):
    # The queue payload carries an is_admin flag so the page can reveal the
    # admin-panel link only for admins. The caller id is derived server-side.
    async def _is_admin_for(admin_ids: tuple[int, ...]) -> bool:
        srv = await start_webapp_server(
            host="127.0.0.1", port=0, store=store, bot_token=TOKEN, admin_ids=admin_ids
        )
        try:
            async with ClientSession() as s:
                async with s.get(
                    srv.url("/api/my/queue"), headers={"Authorization": _init_data(USER_A)}
                ) as r:
                    assert r.status == 200
                    body = await r.json()
        finally:
            await srv.close()
        return body["is_admin"]

    assert await _is_admin_for((999,)) is False
    assert await _is_admin_for((USER_A,)) is True
