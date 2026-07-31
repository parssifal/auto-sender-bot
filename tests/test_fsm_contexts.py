from telegram.handlers.contexts import (
    PostContent, DateTimePick, PreviewRef,
    ScheduleContext, RepeatContext, BroadcastContext, DraftContext, EditContext,
    field_names,
)


def test_postcontent_defaults():
    c = PostContent()
    assert c.kind is None and c.caption_above is False
    assert c.media_items == [] and c.text_before_media is False
    assert c.draft_text is None and c.draft_entities_json is None


def test_media_items_not_shared_between_instances():
    a, b = ScheduleContext(), ScheduleContext()
    a.media_items.append({"x": 1})
    assert b.media_items == []  # field(default_factory) — no shared mutable default


def test_schedule_context_field_superset():
    # keys the schedule flow reads/writes (from audit)
    expected = {
        "kind", "text", "entities_json", "caption", "caption_entities_json",
        "caption_above", "text_before_media", "draft_text", "draft_entities_json",
        "media_items", "calendar_year", "calendar_month", "selected_date",
        "scheduled_at_utc", "scheduled_local", "preview_msg_ids", "preview_chat_id",
        "selected_chat_ids", "dest_page",
    }
    assert expected <= field_names(ScheduleContext)


def test_draft_context_extra_fields():
    fn = field_names(DraftContext)
    assert {"chat_id", "team_id", "draft_publish_id", "edit_draft_id"} <= fn
    assert {"draft_text", "draft_entities_json"} <= fn  # inherited from PostContent


def test_edit_context_extra_fields():
    fn = field_names(EditContext)
    assert {"edit_post_id", "edit_draft_id", "edit_preserve_caption_above"} <= fn


def test_repeat_context_has_interval_and_dest_page():
    fn = field_names(RepeatContext)
    assert {"interval_type", "dest_page", "selected_chat_ids"} <= fn


def test_all_contexts_have_preview_ref():
    for cls in (ScheduleContext, RepeatContext, BroadcastContext, DraftContext, EditContext):
        assert {"preview_msg_ids", "preview_chat_id"} <= field_names(cls)


import pytest
from telegram.handlers.helpers import (
    get_schedule_ctx, get_draft_ctx, get_edit_ctx, get_repeat_ctx, get_broadcast_ctx,
)


class FakeState:
    """Minimal stand-in for aiogram FSMContext.get_data()."""

    def __init__(self, data):
        self._data = dict(data)

    async def get_data(self):
        return dict(self._data)


@pytest.mark.asyncio
async def test_getter_hydrates_only_present_keys():
    # simulates an OLD flat-key Redis session captured mid-flow before deploy
    state = FakeState({
        "media_items": [{"type": "photo", "file_id": "abc"}],
        "caption_above": True,
        "draft_text": "hi",
        "selected_chat_ids": [111, 222],
        "dest_page": 2,
        "stray_legacy_key": "ignored",   # unknown keys must not crash hydration
    })
    ctx = await get_schedule_ctx(state)
    assert ctx.media_items == [{"type": "photo", "file_id": "abc"}]
    assert ctx.caption_above is True
    assert ctx.draft_text == "hi"
    assert ctx.selected_chat_ids == [111, 222]
    assert ctx.dest_page == 2
    # keys absent from storage fall back to dataclass defaults
    assert ctx.kind is None
    assert ctx.scheduled_at_utc is None


@pytest.mark.asyncio
async def test_getter_empty_state_all_defaults():
    ctx = await get_draft_ctx(FakeState({}))
    assert ctx.chat_id is None and ctx.team_id is None
    assert ctx.media_items == [] and ctx.caption_above is False


@pytest.mark.asyncio
async def test_edit_getter_reads_edit_fields():
    ctx = await get_edit_ctx(FakeState({
        "edit_post_id": "p1", "edit_preserve_caption_above": True,
        "scheduled_at_utc": 1700000000,
    }))
    assert ctx.edit_post_id == "p1"
    assert ctx.edit_preserve_caption_above is True
    assert ctx.scheduled_at_utc == 1700000000
