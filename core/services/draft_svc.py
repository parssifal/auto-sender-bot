from __future__ import annotations

from core.state import DraftRow, StateStore


async def resolve_publishable_draft(store: StateStore, draft_id: str, user_id: int) -> DraftRow | None:
    permissions = await store.get_draft_permissions(draft_id, user_id)
    if permissions is None or not permissions.can_publish:
        return None
    return await store.get_draft(draft_id)


async def publish_draft(store: StateStore, *, user_id: int, draft: DraftRow, scheduled_at_utc: int) -> str:
    if draft.kind == "text":
        return await store.create_scheduled_text_post(
            user_id=user_id,
            chat_id=draft.chat_id,
            scheduled_at_utc=scheduled_at_utc,
            text=str(draft.text or ""),
            entities_json=draft.entities_json,
        )
    media_items = await store.get_draft_media(draft.id)
    return await store.create_scheduled_media_post(
        user_id=user_id,
        chat_id=draft.chat_id,
        scheduled_at_utc=scheduled_at_utc,
        caption=draft.caption,
        caption_entities_json=draft.caption_entities_json,
        caption_above=None if draft.caption_above is None else bool(draft.caption_above),
        media_items=media_items,
    )
