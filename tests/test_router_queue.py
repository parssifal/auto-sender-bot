from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from core.db import open_db
from core.state import StateStore
from telegram.handlers.keyboards import _queue_paged_kb, _edit_paged_kb, _delete_paged_kb

USER_ID = 1001
CHAT_ID = -2001

LANG = "en"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_posts(n: int) -> list[dict[str, str]]:
    return [{"id": f"post-{i}", "label": f"Post {i}"} for i in range(n)]


# ---------------------------------------------------------------------------
# _queue_paged_kb
# ---------------------------------------------------------------------------

class TestQueuePagedKb:
    def test_first_page_with_more_has_only_forward_button(self) -> None:
        kb = _queue_paged_kb(_make_posts(1), page=0, has_more=True, lang=LANG)
        flat = [btn for row in kb.inline_keyboard for btn in row]
        nav = [btn for btn in flat if btn.callback_data and btn.callback_data.startswith("qpage:")]
        assert len(nav) == 1
        assert nav[0].callback_data == "qpage:1"

    def test_middle_page_has_both_nav_buttons(self) -> None:
        kb = _queue_paged_kb(_make_posts(1), page=1, has_more=True, lang=LANG)
        nav_data = [
            btn.callback_data
            for row in kb.inline_keyboard
            for btn in row
            if btn.callback_data and btn.callback_data.startswith("qpage:")
        ]
        assert "qpage:0" in nav_data
        assert "qpage:2" in nav_data

    def test_last_page_has_only_back_button(self) -> None:
        kb = _queue_paged_kb(_make_posts(1), page=1, has_more=False, lang=LANG)
        nav = [
            btn
            for row in kb.inline_keyboard
            for btn in row
            if btn.callback_data and btn.callback_data.startswith("qpage:")
        ]
        assert len(nav) == 1
        assert nav[0].callback_data == "qpage:0"

    def test_single_page_has_no_nav_buttons(self) -> None:
        kb = _queue_paged_kb(_make_posts(1), page=0, has_more=False, lang=LANG)
        nav = [
            btn
            for row in kb.inline_keyboard
            for btn in row
            if btn.callback_data and btn.callback_data.startswith("qpage:")
        ]
        assert nav == []

    def test_post_buttons_use_qview_and_qcancel_callbacks(self) -> None:
        posts = _make_posts(2)
        kb = _queue_paged_kb(posts, page=0, has_more=False, lang=LANG)
        all_data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert "qview:post-0" in all_data
        assert "qcancel:post-0" in all_data
        assert "qview:post-1" in all_data


# ---------------------------------------------------------------------------
# _edit_paged_kb
# ---------------------------------------------------------------------------

class TestEditPagedKb:
    def test_first_page_with_more_has_only_forward_button(self) -> None:
        kb = _edit_paged_kb(_make_posts(1), page=0, has_more=True, lang=LANG)
        nav = [
            btn
            for row in kb.inline_keyboard
            for btn in row
            if btn.callback_data and btn.callback_data.startswith("epage:")
        ]
        assert len(nav) == 1
        assert nav[0].callback_data == "epage:1"

    def test_last_page_has_only_back_button(self) -> None:
        kb = _edit_paged_kb(_make_posts(1), page=1, has_more=False, lang=LANG)
        nav = [
            btn
            for row in kb.inline_keyboard
            for btn in row
            if btn.callback_data and btn.callback_data.startswith("epage:")
        ]
        assert len(nav) == 1
        assert nav[0].callback_data == "epage:0"

    def test_single_page_has_no_nav_buttons(self) -> None:
        kb = _edit_paged_kb(_make_posts(1), page=0, has_more=False, lang=LANG)
        nav = [
            btn
            for row in kb.inline_keyboard
            for btn in row
            if btn.callback_data and btn.callback_data.startswith("epage:")
        ]
        assert nav == []

    def test_post_buttons_use_qedit_callbacks(self) -> None:
        posts = _make_posts(2)
        kb = _edit_paged_kb(posts, page=0, has_more=False, lang=LANG)
        all_data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert "qedit:post-0" in all_data
        assert "qedit:post-1" in all_data


# ---------------------------------------------------------------------------
# _delete_paged_kb
# ---------------------------------------------------------------------------

class TestDeletePagedKb:
    def test_first_page_with_more_has_only_forward_button(self) -> None:
        kb = _delete_paged_kb(_make_posts(1), page=0, has_more=True, lang=LANG)
        nav = [
            btn
            for row in kb.inline_keyboard
            for btn in row
            if btn.callback_data and btn.callback_data.startswith("delpage:")
        ]
        assert len(nav) == 1
        assert nav[0].callback_data == "delpage:1"

    def test_last_page_has_only_back_button(self) -> None:
        kb = _delete_paged_kb(_make_posts(1), page=1, has_more=False, lang=LANG)
        nav = [
            btn
            for row in kb.inline_keyboard
            for btn in row
            if btn.callback_data and btn.callback_data.startswith("delpage:")
        ]
        assert len(nav) == 1
        assert nav[0].callback_data == "delpage:0"

    def test_single_page_has_no_nav_buttons(self) -> None:
        kb = _delete_paged_kb(_make_posts(1), page=0, has_more=False, lang=LANG)
        nav = [
            btn
            for row in kb.inline_keyboard
            for btn in row
            if btn.callback_data and btn.callback_data.startswith("delpage:")
        ]
        assert nav == []

    def test_post_buttons_use_qdelask_callbacks(self) -> None:
        posts = _make_posts(2)
        kb = _delete_paged_kb(posts, page=0, has_more=False, lang=LANG)
        all_data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert "qdelask:post-0" in all_data
        assert "qdelask:post-1" in all_data


# ---------------------------------------------------------------------------
# state.py offset parameter
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def store() -> StateStore:
    conn = await open_db(":memory:")
    state = StateStore(conn)
    await state.migrate()
    await state.ensure_user(USER_ID)
    await state.upsert_destination(
        CHAT_ID,
        "channel",
        "Test channel",
        "test_channel",
        "administrator",
        True,
    )
    yield state
    await conn.close()


@pytest.mark.asyncio
async def test_list_pending_posts_offset_skips_earlier_posts(store: StateStore) -> None:
    base_ts = int(datetime(2099, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp())
    ids = []
    for i in range(4):
        pid = await store.create_scheduled_text_post(
            user_id=USER_ID,
            chat_id=CHAT_ID,
            scheduled_at_utc=base_ts + i * 60,
            text=f"Post {i}",
            entities_json=None,
        )
        ids.append(pid)

    first_page = await store.list_pending_posts(USER_ID, limit=2, offset=0)
    second_page = await store.list_pending_posts(USER_ID, limit=2, offset=2)

    assert [p.id for p in first_page] == ids[:2]
    assert [p.id for p in second_page] == ids[2:]


@pytest.mark.asyncio
async def test_list_pending_posts_offset_beyond_end_returns_empty(store: StateStore) -> None:
    base_ts = int(datetime(2099, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp())
    await store.create_scheduled_text_post(
        user_id=USER_ID,
        chat_id=CHAT_ID,
        scheduled_at_utc=base_ts,
        text="Only post",
        entities_json=None,
    )

    result = await store.list_pending_posts(USER_ID, limit=10, offset=10)

    assert result == []


class _FakeMessage:
    def __init__(self) -> None:
        self.reply_markup = None

    async def answer(self, _text, reply_markup=None):
        self.reply_markup = reply_markup

    async def edit_text(self, _text, reply_markup=None):
        self.reply_markup = reply_markup


@pytest.mark.asyncio
async def test_render_queue_page_excludes_recurring_instances(store: StateStore) -> None:
    from telegram.handlers.queue import _render_queue_page

    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    editable_id = await store.create_scheduled_text_post(
        user_id=USER_ID,
        chat_id=CHAT_ID,
        scheduled_at_utc=scheduled_at_utc,
        text="Editable",
        entities_json=None,
    )
    _, recurring_id = await store.create_recurring_series(
        user_id=USER_ID,
        chat_id=CHAT_ID,
        interval_type="daily",
        time_of_day_minutes=9 * 60,
        timezone="Europe/Moscow",
        start_at_utc=scheduled_at_utc + 3600,
        kind="text",
        text="Recurring",
        entities_json=None,
    )

    msg = _FakeMessage()
    await _render_queue_page(store, msg, page=0, user_id=USER_ID)

    all_data = [
        btn.callback_data
        for row in msg.reply_markup.inline_keyboard
        for btn in row
        if btn.callback_data
    ]
    assert f"qview:{editable_id}" in all_data
    assert f"qcancel:{recurring_id}" not in all_data
    assert f"qview:{recurring_id}" not in all_data


@pytest.mark.asyncio
async def test_list_editable_pending_posts_offset_skips_earlier_posts(store: StateStore) -> None:
    base_ts = int(datetime(2099, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp())
    ids = []
    for i in range(3):
        pid = await store.create_scheduled_text_post(
            user_id=USER_ID,
            chat_id=CHAT_ID,
            scheduled_at_utc=base_ts + i * 60,
            text=f"Post {i}",
            entities_json=None,
        )
        ids.append(pid)

    page0 = await store.list_editable_pending_posts(USER_ID, limit=2, offset=0)
    page1 = await store.list_editable_pending_posts(USER_ID, limit=2, offset=2)

    assert [p.id for p in page0] == ids[:2]
    assert [p.id for p in page1] == ids[2:]
