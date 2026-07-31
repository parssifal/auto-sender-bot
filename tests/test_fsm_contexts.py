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
    assert {"chat_id", "team_id", "draft_publish_id"} <= fn
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
