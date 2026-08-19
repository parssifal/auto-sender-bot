from __future__ import annotations

import time

import pytest

from core.limits import REFERRAL_BONUS_CAP_DAYS, REFERRAL_BONUS_DAYS
from core.state import StateStore

_DAY = 86400
NOW = 1_700_000_000


async def _fresh_user(store: StateStore, user_id: int) -> None:
    """ensure_user leaves created_at == updated_at, which capture_referral needs."""
    await store.ensure_user(user_id)


# --- capture_referral (anti-abuse) ---


@pytest.mark.asyncio
async def test_self_referral_rejected(store: StateStore) -> None:
    await _fresh_user(store, 1)
    assert await store.capture_referral(1, 1) is False
    prof = await store.get_user_profile(1)
    assert prof["referred_by"] is None


@pytest.mark.asyncio
async def test_capture_requires_existing_referrer(store: StateStore) -> None:
    await _fresh_user(store, 2)  # referrer 999 does not exist
    assert await store.capture_referral(2, 999) is False
    assert (await store.get_user_profile(2))["referred_by"] is None


@pytest.mark.asyncio
async def test_capture_once_and_not_overwritten(store: StateStore) -> None:
    await _fresh_user(store, 10)  # referrer
    await _fresh_user(store, 11)  # referrer 2
    await _fresh_user(store, 2)  # referee
    assert await store.capture_referral(2, 10) is True
    assert (await store.get_user_profile(2))["referred_by"] == 10

    # A later /start re-runs ensure_user (advancing updated_at); a second capture
    # attempt with a different referrer must NOT overwrite.
    await store.ensure_user(2)
    assert await store.capture_referral(2, 11) is False
    assert (await store.get_user_profile(2))["referred_by"] == 10


@pytest.mark.asyncio
async def test_existing_user_cannot_attach_referrer(store: StateStore) -> None:
    await _fresh_user(store, 10)  # referrer
    await _fresh_user(store, 2)  # referee, created earlier
    # Simulate the referee already being an active user (updated_at advanced).
    await store._conn.execute("UPDATE users SET updated_at=updated_at+100 WHERE user_id=2")
    await store._conn.commit()
    assert await store.capture_referral(2, 10) is False
    assert (await store.get_user_profile(2))["referred_by"] is None


# --- grant_referral_bonus (activation payout) ---


@pytest.mark.asyncio
async def test_bonus_paid_to_both_and_idempotent(store: StateStore) -> None:
    await _fresh_user(store, 10)  # referrer (basic)
    await _fresh_user(store, 2)  # referee (basic)
    assert await store.capture_referral(2, 10) is True

    result = await store.grant_referral_bonus(referee_id=2, now=NOW)
    assert result is not None
    assert result.referrer_id == 10
    assert result.referee_days == REFERRAL_BONUS_DAYS
    assert result.referrer_days == REFERRAL_BONUS_DAYS

    # Both are now pro, expiring +7 days from NOW.
    for uid in (2, 10):
        prof = await store.get_user_profile(uid)
        assert prof["plan"] == "pro"
        assert prof["plan_expires_at"] == NOW + REFERRAL_BONUS_DAYS * _DAY

    # Idempotent: a second delivered post pays nothing.
    assert await store.grant_referral_bonus(referee_id=2, now=NOW + 10) is None


@pytest.mark.asyncio
async def test_bonus_noop_without_referrer(store: StateStore) -> None:
    await _fresh_user(store, 5)
    assert await store.grant_referral_bonus(referee_id=5, now=NOW) is None
    assert await store.get_user_plan(5) == "basic"


@pytest.mark.asyncio
async def test_cap_stops_further_payout(store: StateStore) -> None:
    await _fresh_user(store, 10)  # referrer
    # Referrer already granted all but 3 days of the 90-day cap.
    await store._conn.execute(
        "UPDATE users SET referral_bonus_days=? WHERE user_id=10",
        (REFERRAL_BONUS_CAP_DAYS - 3,),
    )
    await store._conn.commit()
    await _fresh_user(store, 2)
    assert await store.capture_referral(2, 10) is True

    result = await store.grant_referral_bonus(referee_id=2, now=NOW)
    assert result is not None
    assert result.referee_days == REFERRAL_BONUS_DAYS  # referee had headroom
    assert result.referrer_days == 3  # clamped to the remaining cap

    # Referrer is now at the cap; a new referee's activation pays them nothing.
    await _fresh_user(store, 3)
    assert await store.capture_referral(3, 10) is True
    result2 = await store.grant_referral_bonus(referee_id=3, now=NOW + 100)
    assert result2 is not None
    assert result2.referrer_days == 0  # at cap
    assert result2.referee_days == REFERRAL_BONUS_DAYS


@pytest.mark.asyncio
async def test_expired_and_active_pro_are_extended_not_downgraded(store: StateStore) -> None:
    await _fresh_user(store, 10)  # referrer with an ACTIVE pro plan in the future
    future = NOW + 100 * _DAY
    await store.set_user_plan(10, "pro", future)
    await _fresh_user(store, 2)  # referee with an EXPIRED pro plan
    await store._conn.execute(
        "UPDATE users SET plan='pro', plan_expires_at=? WHERE user_id=2", (NOW - _DAY,)
    )
    await store._conn.commit()
    assert await store.capture_referral(2, 10) is True

    result = await store.grant_referral_bonus(referee_id=2, now=NOW)
    assert result is not None
    # Referee (expired -> basic) restarts pro from NOW.
    assert (await store.get_user_profile(2))["plan_expires_at"] == NOW + REFERRAL_BONUS_DAYS * _DAY
    # Referrer (active pro) has expiry EXTENDED from its future value, not lowered.
    assert (await store.get_user_profile(10))["plan_expires_at"] == future + REFERRAL_BONUS_DAYS * _DAY


@pytest.mark.asyncio
async def test_premium_recipient_not_downgraded(store: StateStore) -> None:
    await _fresh_user(store, 10)  # referrer on premium
    prem_expiry = NOW + 200 * _DAY
    await store.set_user_plan(10, "premium", prem_expiry)
    await _fresh_user(store, 2)
    assert await store.capture_referral(2, 10) is True

    result = await store.grant_referral_bonus(referee_id=2, now=NOW)
    assert result is not None
    assert result.referrer_days == 0  # premium beats the pro bonus -> untouched
    prof = await store.get_user_profile(10)
    assert prof["plan"] == "premium"
    assert prof["plan_expires_at"] == prem_expiry


@pytest.mark.asyncio
async def test_referral_stats_in_profile(store: StateStore) -> None:
    await _fresh_user(store, 10)  # referrer
    for uid in (2, 3, 4):
        await _fresh_user(store, uid)
        assert await store.capture_referral(uid, 10) is True
    # Only user 2 activates.
    await store.grant_referral_bonus(referee_id=2, now=NOW)

    prof = await store.get_user_profile(10)
    assert prof["referred_count"] == 3
    assert prof["referred_activated"] == 1
    assert (await store.get_user_profile(2))["referred_by"] == 10


# --- scheduler activation hook (best-effort, must never fail the post) ---

CHAT_ID = -1001
REFERRER = 10
REFEREE = 2


class _FakeBot:
    """Records API calls. ``fail_dm`` fails only DMs (positive chat_id), not the
    channel post (negative chat_id)."""

    def __init__(self, *, fail_dm: bool = False) -> None:
        self.calls: list[dict] = []
        self.fail_dm = fail_dm

    async def me(self):
        return type("Me", (), {"id": 777})()

    async def get_chat_member(self, **kwargs):
        return type("M", (), {"status": "administrator", "can_post_messages": True})()

    async def send_message(self, **kwargs):
        if self.fail_dm and int(kwargs.get("chat_id", 0)) > 0:
            raise RuntimeError("dm failed")
        self.calls.append(kwargs)
        return True


async def _seed_referral_post(store: StateStore) -> str:
    from core.state import StateStore as _SS  # noqa: F401 (type only)

    await store.ensure_user(REFERRER)
    await store.ensure_user(REFEREE)
    assert await store.capture_referral(REFEREE, REFERRER) is True
    await store.upsert_destination(CHAT_ID, "channel", "Ch", "ch", "administrator", True)
    return await store.create_scheduled_text_post(
        user_id=REFEREE, chat_id=CHAT_ID, scheduled_at_utc=NOW - 10, text="hi", entities_json=None
    )


@pytest.mark.asyncio
async def test_scheduler_first_post_pays_both(store: StateStore) -> None:
    from core.scheduler import _process_due_post

    post_id = await _seed_referral_post(store)
    post = await store.get_scheduled_post(post_id)
    bot = _FakeBot()
    await _process_due_post(bot=bot, store=store, post=post, now_utc=int(time.time()))

    assert (await store.get_scheduled_post(post_id)).status == "sent"
    assert await store.get_user_plan(REFEREE) == "pro"
    assert await store.get_user_plan(REFERRER) == "pro"
    # One channel post + two bonus DMs.
    dms = [c for c in bot.calls if int(c["chat_id"]) > 0]
    assert {int(c["chat_id"]) for c in dms} == {REFEREE, REFERRER}


@pytest.mark.asyncio
async def test_scheduler_notify_failure_does_not_fail_post(store: StateStore) -> None:
    from core.scheduler import _process_due_post

    post_id = await _seed_referral_post(store)
    post = await store.get_scheduled_post(post_id)
    bot = _FakeBot(fail_dm=True)  # every bonus DM raises
    await _process_due_post(bot=bot, store=store, post=post, now_utc=int(time.time()))

    # Post is still delivered, and the (transactional) bonus still landed.
    assert (await store.get_scheduled_post(post_id)).status == "sent"
    assert await store.get_user_plan(REFEREE) == "pro"
    assert await store.get_user_plan(REFERRER) == "pro"


@pytest.mark.asyncio
async def test_scheduler_bonus_error_does_not_fail_post(store: StateStore, monkeypatch) -> None:
    from core.scheduler import _process_due_post

    post_id = await _seed_referral_post(store)
    post = await store.get_scheduled_post(post_id)

    async def _boom(*args, **kwargs):
        raise RuntimeError("bookkeeping exploded")

    monkeypatch.setattr(store, "grant_referral_bonus", _boom)
    bot = _FakeBot()
    await _process_due_post(bot=bot, store=store, post=post, now_utc=int(time.time()))

    assert (await store.get_scheduled_post(post_id)).status == "sent"
