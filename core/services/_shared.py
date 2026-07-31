from __future__ import annotations

from core.state import DraftRow


def _resolve_draft_id(drafts: list[DraftRow], draft_ref: str) -> str | None:
    ref = draft_ref.strip().lower()
    if not ref:
        return None

    for draft in drafts:
        if draft.id == ref:
            return draft.id

    matches = [draft.id for draft in drafts if draft.id.startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    return None


def _normalize_selected_chat_ids(raw_value: object) -> list[int]:
    if not isinstance(raw_value, list):
        return []

    selected: set[int] = set()
    for item in raw_value:
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            selected.add(item)
            continue
        if isinstance(item, str):
            try:
                selected.add(int(item))
            except ValueError:
                continue
    return sorted(selected)


def _destination_label(title: str, username: str | None) -> str:
    if username:
        return f"{title} (@{username})"
    return title
