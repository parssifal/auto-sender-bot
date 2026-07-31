from __future__ import annotations


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
