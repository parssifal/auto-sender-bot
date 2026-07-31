from __future__ import annotations

from core.services._shared import _destination_label, _normalize_selected_chat_ids
from core.state import StateStore


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
