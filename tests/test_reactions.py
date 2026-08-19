from __future__ import annotations

import time

import pytest

import core.reactions as reactions
from core.db import open_db
from core.scheduler import _process_due_post
from core.state import StateStore

USER_ID = 123
CHAT_ID = -1001


# --- palette / cap enforcement (server-side, never trust the client) -------- #

def test_cap_by_plan_basic_pro_premium() -> None:
    # Within cap passes.
    assert reactions.sanitize_emojis("basic", ["👍"]) == ["👍"]
    assert reactions.sanitize_emojis("pro", ["👍", "😂"]) == ["👍", "😂"]
    assert reactions.sanitize_emojis("premium", ["🦄", "🎃", "🥑"]) == ["🦄", "🎃", "🥑"]
    # One over the cap is rejected for each plan (basic 1 / pro 2 / premium 3).
    for plan, over in (("basic", ["👍", "❤️"]), ("pro", ["👍", "❤️", "🔥"]), ("premium", ["🦄", "🎃", "🥑", "🐙"])):
        with pytest.raises(reactions.ReactionValidationError) as exc:
            reactions.sanitize_emojis(plan, over)
        assert exc.value.reason == "too_many_reactions"


def test_palette_membership_rejected() -> None:
    with pytest.raises(reactions.ReactionValidationError) as exc:
        reactions.sanitize_emojis("basic", ["😂"])  # not in basic palette
    assert exc.value.reason == "emoji_not_allowed"
    with pytest.raises(reactions.ReactionValidationError) as exc:
        reactions.sanitize_emojis("pro", ["🦄"])  # not in pro palette
    assert exc.value.reason == "emoji_not_allowed"
    # Premium accepts any emoji, but not plain text typed into the free input.
    assert reactions.sanitize_emojis("premium", ["🦄"]) == ["🦄"]
    for junk in ("hello", "123", "a", ":fire:", ""):
        with pytest.raises(reactions.ReactionValidationError) as exc:
            reactions.sanitize_emojis("premium", [junk])
        assert exc.value.reason == "emoji_not_allowed"
    # A keycap emoji carries an ASCII digit but is still a real emoji — kept.
    assert reactions.sanitize_emojis("premium", ["1️⃣"]) == ["1️⃣"]


def test_display_palette_premium_is_concrete() -> None:
    # Validation whitelist is None (any emoji) but the UI quick set is concrete —
    # premium must never be handed an empty palette row.
    assert reactions.palette_for("premium") is None
    assert reactions.display_palette("premium") == reactions.PREMIUM_PALETTE
    assert reactions.display_palette("basic") == reactions.BASIC_PALETTE
    assert reactions.display_palette("pro") == reactions.PRO_PALETTE


def test_dedupe_preserves_order() -> None:
    assert reactions.sanitize_emojis("pro", ["👍", "👍", "😂"]) == ["👍", "😂"]


def test_presets_allowed_by_plan() -> None:
    assert reactions.presets_allowed("basic") is False
    assert reactions.presets_allowed("pro") is True
    assert reactions.presets_allowed("premium") is True


# --- preset storage --------------------------------------------------------- #

@pytest.mark.asyncio
async def test_preset_upsert_get_list_delete() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        await store.ensure_user(USER_ID)

        await store.upsert_reaction_preset(USER_ID, "весёлый", '["👍","🔥"]')
        assert await store.get_reaction_preset(USER_ID, "весёлый") == '["👍","🔥"]'

        # Upsert overwrites the same (user_id, post_type).
        await store.upsert_reaction_preset(USER_ID, "весёлый", '["❤️"]')
        assert await store.get_reaction_preset(USER_ID, "весёлый") == '["❤️"]'

        await store.upsert_reaction_preset(USER_ID, "грустный", '["🙏"]')
        listed = await store.list_reaction_presets(USER_ID)
        assert {p["post_type"] for p in listed} == {"весёлый", "грустный"}

        assert await store.delete_reaction_preset(USER_ID, "весёлый") is True
        assert await store.get_reaction_preset(USER_ID, "весёлый") is None
        assert await store.delete_reaction_preset(USER_ID, "весёлый") is False  # already gone
    finally:
        await conn.close()


# --- scheduler: a reaction error must never fail a delivered post ----------- #

class _Msg:
    def __init__(self, message_id: int) -> None:
        self.message_id = message_id


class _ReactBot:
    def __init__(self, *, reaction_raises: bool) -> None:
        self._reaction_raises = reaction_raises
        self.reaction_calls: list[dict] = []

    async def me(self):
        return type("Me", (), {"id": 777})()

    async def get_chat_member(self, **kwargs):
        return type("Member", (), {"status": "administrator", "can_post_messages": True})()

    async def send_message(self, **kwargs):
        return _Msg(555)

    async def set_message_reaction(self, **kwargs):
        self.reaction_calls.append(kwargs)
        if self._reaction_raises:
            raise RuntimeError("reaction rejected by Telegram")
        return True


async def _seed(store: StateStore) -> None:
    await store.ensure_user(USER_ID)
    await store.upsert_destination(CHAT_ID, "channel", "Dest", "dest", "administrator", True)


@pytest.mark.asyncio
async def test_reaction_failure_does_not_fail_post() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        await _seed(store)
        due_at = int(time.time()) - 10
        post_id = await store.create_scheduled_text_post(
            USER_ID, CHAT_ID, due_at, "hello", None, reaction_emojis_json='["👍"]'
        )
        post = await store.get_scheduled_post(post_id)
        bot = _ReactBot(reaction_raises=True)

        await _process_due_post(bot=bot, store=store, post=post, now_utc=due_at)

        final = await store.get_scheduled_post(post_id)
        assert final.status == "sent"          # delivered post stays sent
        assert bot.reaction_calls               # seeding was attempted
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_reaction_seeded_on_first_message_on_success() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        await _seed(store)
        due_at = int(time.time()) - 10
        post_id = await store.create_scheduled_text_post(
            USER_ID, CHAT_ID, due_at, "hello", None, reaction_emojis_json='["👍","🔥"]'
        )
        post = await store.get_scheduled_post(post_id)
        bot = _ReactBot(reaction_raises=False)

        await _process_due_post(bot=bot, store=store, post=post, now_utc=due_at)

        assert (await store.get_scheduled_post(post_id)).status == "sent"
        assert len(bot.reaction_calls) == 1
        call = bot.reaction_calls[0]
        assert call["chat_id"] == CHAT_ID
        assert call["message_id"] == 555            # first message id from SendStats
        assert [r.emoji for r in call["reaction"]] == ["👍", "🔥"]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_no_reaction_call_when_post_has_none() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        await _seed(store)
        due_at = int(time.time()) - 10
        post_id = await store.create_scheduled_text_post(USER_ID, CHAT_ID, due_at, "hello", None)
        post = await store.get_scheduled_post(post_id)
        bot = _ReactBot(reaction_raises=False)

        await _process_due_post(bot=bot, store=store, post=post, now_utc=due_at)

        assert (await store.get_scheduled_post(post_id)).status == "sent"
        assert bot.reaction_calls == []
    finally:
        await conn.close()
