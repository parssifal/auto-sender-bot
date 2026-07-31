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
    """Minimal stand-in for aiogram FSMContext.get_data()/update_data()."""

    def __init__(self, data):
        self._data = dict(data)

    async def get_data(self):
        return dict(self._data)

    async def update_data(self, **changes):
        self._data.update(changes)
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


# --- Typed FSM writes (Phase 4 / 4b) -------------------------------------

from telegram.handlers.helpers import (  # noqa: E402
    patch_ctx, patch_schedule_ctx, patch_draft_ctx, patch_edit_ctx,
    patch_broadcast_ctx, patch_preview_ctx, patch_content_ctx, _ctx_cls_for_state,
)
from telegram.handlers.states import (  # noqa: E402
    ScheduleStates, RepeatStates, BroadcastStates, DraftStates, EditStates,
)


@pytest.mark.asyncio
async def test_patch_writes_valid_keys():
    state = FakeState({})
    await patch_schedule_ctx(state, chat_id=42, dest_page=1, selected_date=None)
    assert await state.get_data() == {"chat_id": 42, "dest_page": 1, "selected_date": None}


@pytest.mark.asyncio
async def test_patch_rejects_unknown_key_and_writes_nothing():
    state = FakeState({})
    with pytest.raises(KeyError):
        await patch_schedule_ctx(state, chta_id=42)  # deliberate typo
    assert await state.get_data() == {}  # validation happens before the write


@pytest.mark.asyncio
async def test_edit_context_carries_chat_id_write():
    # chat_id is written by the edit flow; before 4b it was absent from EditContext
    # and silently dropped by get_edit_ctx. It must now round-trip.
    assert "chat_id" in field_names(EditContext)
    state = FakeState({})
    await patch_edit_ctx(state, edit_post_id="p", chat_id=7)
    ctx = await get_edit_ctx(state)
    assert ctx.chat_id == 7


@pytest.mark.asyncio
async def test_draft_context_carries_dest_page_write():
    assert "dest_page" in field_names(DraftContext)
    state = FakeState({})
    await patch_draft_ctx(state, dest_page=3)
    assert (await get_draft_ctx(state)).dest_page == 3


def test_ctx_cls_for_state_maps_each_flow():
    assert _ctx_cls_for_state(RepeatStates.entering_datetime.state) is RepeatContext
    assert _ctx_cls_for_state(BroadcastStates.entering_datetime.state) is BroadcastContext
    assert _ctx_cls_for_state(DraftStates.entering_datetime.state) is DraftContext
    assert _ctx_cls_for_state(EditStates.entering_datetime.state) is EditContext
    assert _ctx_cls_for_state(ScheduleStates.entering_datetime.state) is ScheduleContext


@pytest.mark.asyncio
async def test_patch_content_ctx_validates_against_resolved_flow():
    # selected_chat_ids is not a DraftContext field → a draft-state write must fail,
    # but the same key is valid once the flow resolves to the broadcast context.
    state = FakeState({})
    with pytest.raises(KeyError):
        await patch_content_ctx(state, DraftStates.collecting_post.state, selected_chat_ids=[1])
    assert await state.get_data() == {}
    await patch_content_ctx(state, BroadcastStates.collecting_post.state, selected_chat_ids=[1])
    assert (await state.get_data())["selected_chat_ids"] == [1]


@pytest.mark.asyncio
async def test_patch_preview_ctx_rejects_non_preview_key():
    state = FakeState({})
    await patch_preview_ctx(state, preview_msg_ids=[1], preview_chat_id=9)
    with pytest.raises(KeyError):
        await patch_preview_ctx(state, chat_id=1)  # not a PreviewRef field
