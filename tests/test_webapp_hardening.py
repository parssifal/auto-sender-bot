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
from core.webapp import _RateLimiter, _client_key, start_webapp_server

TOKEN = "123456:test-token"
ADMIN_ID = 42


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
    yield state
    await conn.close()


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


# --------------------------------------------------------------------------- #
# _RateLimiter unit tests
# --------------------------------------------------------------------------- #

def test_rate_limiter_allows_up_to_limit_then_blocks() -> None:
    rl = _RateLimiter(max_requests=3, window_seconds=60.0)
    now = 1000.0
    assert rl.allow("1.2.3.4", now=now) is True
    assert rl.allow("1.2.3.4", now=now) is True
    assert rl.allow("1.2.3.4", now=now) is True
    assert rl.allow("1.2.3.4", now=now) is False  # 4th in window -> blocked


def test_rate_limiter_resets_after_window() -> None:
    rl = _RateLimiter(max_requests=1, window_seconds=10.0)
    assert rl.allow("k", now=100.0) is True
    assert rl.allow("k", now=105.0) is False
    assert rl.allow("k", now=111.0) is True  # new window


def test_rate_limiter_is_per_key() -> None:
    rl = _RateLimiter(max_requests=1, window_seconds=60.0)
    assert rl.allow("a", now=1.0) is True
    assert rl.allow("b", now=1.0) is True  # different key, own budget


def test_rate_limiter_bounds_tracked_keys() -> None:
    # The limiter must not grow unbounded when hit with many distinct keys
    # (otherwise the limiter itself is a memory-exhaustion vector).
    rl = _RateLimiter(max_requests=5, window_seconds=60.0, max_keys=100)
    for i in range(1000):
        rl.allow(f"key-{i}", now=1.0)
    assert rl.tracked_key_count() <= 100


# --------------------------------------------------------------------------- #
# _client_key: rate-limit key behind our reverse proxy
# --------------------------------------------------------------------------- #

class _FakeReq:
    def __init__(self, *, xff: str | None = None, remote: str | None = "127.0.0.1"):
        self.headers = {} if xff is None else {"X-Forwarded-For": xff}
        self.remote = remote


def test_client_key_uses_last_xff_hop() -> None:
    # Two different real clients behind the proxy get two different buckets,
    # even though request.remote is 127.0.0.1 for both.
    a = _client_key(_FakeReq(xff="203.0.113.7"))
    b = _client_key(_FakeReq(xff="198.51.100.9"))
    assert a == "203.0.113.7"
    assert b == "198.51.100.9"
    assert a != b


def test_client_key_ignores_spoofed_first_hop() -> None:
    # Caddy appends the real peer as the LAST hop; a client-supplied first hop
    # must not let an attacker share/poison a victim's bucket. Two requests
    # with different real last hops stay separate regardless of the forged head.
    victim = _client_key(_FakeReq(xff="203.0.113.7"))
    attacker = _client_key(_FakeReq(xff="203.0.113.7, 198.51.100.9"))
    assert attacker == "198.51.100.9"
    assert attacker != victim


def test_client_key_falls_back_to_remote_without_xff() -> None:
    assert _client_key(_FakeReq(xff=None, remote="10.0.0.5")) == "10.0.0.5"
    assert _client_key(_FakeReq(xff=None, remote=None)) == "unknown"


# --------------------------------------------------------------------------- #
# Integration: server-level hardening
# --------------------------------------------------------------------------- #

@pytest_asyncio.fixture
async def server(store: StateStore):
    srv = await start_webapp_server(
        host="127.0.0.1", port=0, store=store,
        bot_token=TOKEN, admin_ids=(ADMIN_ID,), bot=_FakeBot(),
        max_body_bytes=8192,
        broadcast_text_max=512,
        rate_limit_max=5,
        rate_limit_window_s=60.0,
    )
    yield srv
    await srv.close()


@pytest.mark.asyncio
async def test_empty_bot_token_rejected(store) -> None:
    # With an empty token, check_webapp_signature computes HMAC over b"" and any
    # user_id can be forged. Refuse to start rather than trust a blank token.
    with pytest.raises(ValueError, match="bot_token must be non-empty"):
        await start_webapp_server(
            host="127.0.0.1", port=0, store=store,
            bot_token="", admin_ids=(ADMIN_ID,), bot=_FakeBot(),
        )


@pytest.mark.asyncio
async def test_oversized_body_rejected(server) -> None:
    url = server.url("/api/broadcast")
    big = {"text": "x" * 9000}  # raw body exceeds max_body_bytes=8192
    async with ClientSession() as s:
        async with s.post(url, json=big, headers={"Authorization": _init_data(ADMIN_ID)}) as r:
            assert r.status == 413


@pytest.mark.asyncio
async def test_rate_limit_returns_429(server) -> None:
    async with ClientSession() as s:
        statuses = []
        for _ in range(8):  # limit is 5/window
            async with s.get(server.url("/")) as r:
                statuses.append(r.status)
    assert 429 in statuses
    assert statuses.count(200) <= 5


@pytest.mark.asyncio
async def test_server_header_does_not_leak_version(server) -> None:
    async with ClientSession() as s:
        async with s.get(server.url("/")) as r:
            server_header = r.headers.get("Server", "")
    assert "aiohttp" not in server_header.lower()
    assert "python" not in server_header.lower()


@pytest.mark.asyncio
async def test_broadcast_rejects_overlong_text(server) -> None:
    url = server.url("/api/broadcast")
    # Under the raw body cap (8192) but over the semantic text cap (512).
    payload = {"text": "y" * 1000}
    async with ClientSession() as s:
        async with s.post(url, json=payload, headers={"Authorization": _init_data(ADMIN_ID)}) as r:
            # rate-limit fixture is generous enough for a single call
            assert r.status == 400
            body = await r.json()
    assert body["error"] == "text_too_long"
