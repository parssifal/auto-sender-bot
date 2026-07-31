from __future__ import annotations

from core.rbac import DraftPermissions
from core.services._shared import _resolve_draft_id
from core.state import DraftRow, StateStore

_NEED_ATTR = {"view": "can_view", "edit": "can_edit", "delete": "can_delete", "publish": "can_publish"}


async def resolve_draft_by_id(
    store: StateStore, draft_id: str, user_id: int, *, need: str
) -> tuple[DraftRow | None, DraftPermissions | None]:
    permissions = await store.get_draft_permissions(draft_id, user_id)
    if permissions is None or not getattr(permissions, _NEED_ATTR[need]):
        return None, permissions
    draft = await store.get_draft(draft_id)
    return draft, permissions


async def resolve_draft_by_ref(
    store: StateStore, user_id: int, draft_ref: str, *, need: str
) -> tuple[DraftRow | None, DraftPermissions | None]:
    drafts = await store.list_drafts(user_id, scope="all", limit=200)
    draft_id = _resolve_draft_id(drafts, draft_ref)
    if draft_id is None:
        return None, None
    return await resolve_draft_by_id(store, draft_id, user_id, need=need)


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
