"""Typed FSM context dataclasses (Phase 4).

These replace ad-hoc ``dict[str, Any]`` access to aiogram FSM data with typed,
autocompleted, typo-proof dataclasses. Composition is via mixins so the fields
touched by the cross-flow media/confirm/datetime/preview handlers are declared
exactly once:

* ``PostContent``  — post body (working + finalized fields), shared by every
  content flow and mutated by the cross-flow media & confirm handlers.
* ``DateTimePick`` — datetime-picker fields, mutated by the shared datetime
  handler.
* ``PreviewRef``   — live-preview message refs, written by ``_send_post_preview``.

Storage stays as **flat keys** (see helpers.get_*_ctx); these dataclasses only
add typed *access* on top, so in-flight sessions survive a mid-flow deploy.

Pure module: imports only the stdlib. No store, no aiogram, no Telegram I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields


@dataclass
class PostContent:
    """Post body — collected/finalized by the cross-flow media & confirm handlers.

    Working fields (during collection): draft_text, draft_entities_json,
      media_items, caption_above, text_before_media.
    Finalized fields (at summary build): kind, text, entities_json, caption,
      caption_entities_json.
    """

    kind: str | None = None                       # "text" | "media"
    text: str | None = None
    entities_json: str | None = None
    caption: str | None = None
    caption_entities_json: str | None = None
    caption_above: bool = False
    text_before_media: bool = False
    draft_text: str | None = None
    draft_entities_json: str | None = None
    media_items: list[dict] = field(default_factory=list)


@dataclass
class DateTimePick:
    calendar_year: int | None = None
    calendar_month: int | None = None
    selected_date: str | None = None
    scheduled_at_utc: int | None = None
    scheduled_local: str | None = None


@dataclass
class PreviewRef:
    preview_msg_ids: list[int] = field(default_factory=list)
    preview_chat_id: int | None = None


@dataclass
class ScheduleContext(PostContent, DateTimePick, PreviewRef):
    selected_chat_ids: list[int] = field(default_factory=list)
    dest_page: int = 0
    chat_id: int | None = None          # single resolved destination (after dest select)


@dataclass
class RepeatContext(PostContent, DateTimePick, PreviewRef):
    selected_chat_ids: list[int] = field(default_factory=list)
    dest_page: int = 0
    interval_type: str | None = None
    chat_id: int | None = None          # single resolved destination


@dataclass
class BroadcastContext(PostContent, DateTimePick, PreviewRef):
    selected_chat_ids: list[int] = field(default_factory=list)
    dest_page: int = 0


@dataclass
class DraftContext(PostContent, DateTimePick, PreviewRef):
    chat_id: int | None = None
    team_id: str | None = None          # team ids are short string ids
    draft_publish_id: str | None = None
    edit_draft_id: str | None = None    # set while editing a draft (DraftStates.editing_post)


@dataclass
class EditContext(PostContent, DateTimePick, PreviewRef):
    edit_post_id: str | None = None
    edit_draft_id: str | None = None
    edit_preserve_caption_above: bool = False


def field_names(cls: type) -> frozenset[str]:
    """Storage keys owned by a context dataclass (for present-key hydration)."""
    return frozenset(f.name for f in fields(cls))
