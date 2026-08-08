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
        store, _FakeBot(), text="hello", entities_json="[]", throttle=0,
    )
    assert summary["delivered"] == 1
    assert seen["args"] == (5, "hello", "[]")


class _FloodWait(Exception):
    """Local stand-in named to match aiogram's TelegramRetryAfter (service detects by name)."""
    def __init__(self, retry_after: float) -> None:
        super().__init__("flood wait")
        self.retry_after = retry_after


_FloodWait.__name__ = "TelegramRetryAfter"


@pytest.mark.asyncio
async def test_broadcast_retries_after_flood_wait(store):
    # T-12: a flood-wait must be honoured (wait + retry), not counted as a lost message.
    for uid in (1, 2):
        await store.ensure_user(uid)
    attempts: dict[int, int] = {}

    async def fake_send(uid):
        attempts[uid] = attempts.get(uid, 0) + 1
        if uid == 2 and attempts[uid] == 1:
            raise _FloodWait(retry_after=0)

    summary = await admin_broadcast_svc.broadcast_to_all(
        store, _FakeBot(), text="hi", send=fake_send, throttle=0,
    )
    assert summary == {"total": 2, "delivered": 2, "blocked": 0, "failed": 0}
    assert attempts[2] == 2  # retried after the flood-wait
