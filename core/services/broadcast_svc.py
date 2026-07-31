from __future__ import annotations

from dataclasses import dataclass, field

from core.services._shared import _destination_label, _normalize_selected_chat_ids
from core.state import StateStore


@dataclass
class PostContent:
    kind: str                                   # "text" | "media"
    text: str | None = None
    entities_json: str | None = None
    caption: str | None = None
    caption_entities_json: str | None = None
    caption_above: bool = False
    media_items: list[dict] = field(default_factory=list)


async def create_broadcast(
    store: StateStore, *, user_id: int, chat_ids: list[int],
    scheduled_at_utc: int, content: PostContent,
) -> list[str]:
    if content.kind == "text":
        return await store.create_broadcast_posts(
            user_id=user_id, chat_ids=chat_ids, scheduled_at_utc=scheduled_at_utc,
            kind="text", text=str(content.text or ""), entities_json=content.entities_json,
        )
    return await store.create_broadcast_posts(
        user_id=user_id, chat_ids=chat_ids, scheduled_at_utc=scheduled_at_utc,
        kind="media", caption=content.caption,
        caption_entities_json=content.caption_entities_json,
        caption_above=bool(content.caption_above), media_items=list(content.media_items),
    )


async def resolve_valid_destinations(
    store: StateStore, user_id: int, selected_chat_ids: list[int]
) -> list[tuple[int, str]]:
    total = await store.count_user_destinations(user_id)
    destinations = (
        await store.list_user_destinations(user_id=user_id, offset=0, limit=total)
        if total > 0 else []
    )
    destination_map = {d.chat_id: d for d in destinations}
    resolved: list[tuple[int, str]] = []
    for chat_id in _normalize_selected_chat_ids(selected_chat_ids):
        d = destination_map.get(chat_id)
        if d is None:
            continue
        resolved.append((chat_id, _destination_label(d.title, d.username)))
    return resolved
