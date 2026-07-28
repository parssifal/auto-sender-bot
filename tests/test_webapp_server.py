from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
import pytest_asyncio
from aiohttp import ClientSession

from core.db import open_db
from core.state import StateStore
from core.webapp import collect_admin_stats, start_webapp_server

TOKEN = "123456:test-token"
ADMIN_ID = 42
NON_ADMIN_ID = 77
CHAT_A = -2001


def _init_data(user_id: int, *, token: str = TOKEN) -> str:
    user = {"id": user_id, "first_name": "Ann"}
    fields = {
        "auth_date": str(int(time.time())),
        "query_id": "AAA",
        "user": json.dumps(user, separators=(",", ":")),
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@pytest_asyncio.fixture
async def store() -> StateStore:
    conn = await open_db(":memory:")
    state = StateStore(conn)
    await state.migrate()
    await state.ensure_user(ADMIN_ID)
    await state.set_user_language(ADMIN_ID, "ru")
    await state.upsert_destination(CHAT_A, "channel", "Ch", None, "administrator", True)
    await state.link_user_destination(ADMIN_ID, CHAT_A, "link")
    yield state
    await conn.close()


@pytest_asyncio.fixture
async def server(store: StateStore):
    srv = await start_webapp_server(
        host="127.0.0.1",
        port=0,
        store=store,
        bot_token=TOKEN,
        admin_ids=(ADMIN_ID,),
    )
    yield srv
    await srv.close()


@pytest.mark.asyncio
async def test_collect_admin_stats_shape(store: StateStore) -> None:
    stats = await collect_admin_stats(store, now=int(time.time()))
    assert stats["total_users"] == 1
    assert stats["avg_channels"] == 1.0
    assert "posts_by_status" in stats
    assert isinstance(stats["daily_new_users"], list)
    assert isinstance(stats["languages"], list)


@pytest.mark.asyncio
async def test_stats_requires_auth(server) -> None:
    async with ClientSession() as session:
        async with session.get(server.url("/api/stats")) as resp:
            assert resp.status == 403


@pytest.mark.asyncio
async def test_stats_ok_for_admin(server) -> None:
    headers = {"Authorization": _init_data(ADMIN_ID)}
    async with ClientSession() as session:
        async with session.get(server.url("/api/stats"), headers=headers) as resp:
            assert resp.status == 200
            payload = await resp.json()
    assert payload["total_users"] == 1


@pytest.mark.asyncio
async def test_stats_forbidden_for_non_admin(server) -> None:
    headers = {"Authorization": _init_data(NON_ADMIN_ID)}
    async with ClientSession() as session:
        async with session.get(server.url("/api/stats"), headers=headers) as resp:
            assert resp.status == 403


@pytest.mark.asyncio
async def test_index_serves_html(server) -> None:
    async with ClientSession() as session:
        async with session.get(server.url("/")) as resp:
            assert resp.status == 200
            assert resp.content_type == "text/html"
            body = await resp.text()
    assert "<html" in body.lower()


@pytest.mark.asyncio
async def test_user_endpoint_returns_profile(server) -> None:
    headers = {"Authorization": _init_data(ADMIN_ID)}
    async with ClientSession() as session:
        async with session.get(server.url(f"/api/user/{ADMIN_ID}"), headers=headers) as resp:
            assert resp.status == 200
            payload = await resp.json()
        assert payload["channels"] == 1
        async with session.get(server.url("/api/user/999999"), headers=headers) as resp:
            assert resp.status == 404
