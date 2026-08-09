from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DraftPermissions:
    can_view: bool
    can_edit: bool
    can_delete: bool
    can_publish: bool


_NO_DRAFT_ACCESS = DraftPermissions(
    can_view=False,
    can_edit=False,
    can_delete=False,
    can_publish=False,
)
_TEAM_DRAFT_MANAGER = DraftPermissions(
    can_view=True,
    can_edit=True,
    can_delete=True,
    can_publish=True,
)
_TEAM_DRAFT_VIEWER = DraftPermissions(
    can_view=True,
    can_edit=False,
    can_delete=False,
    can_publish=False,
)


def resolve_draft_permissions(
    *,
    draft_author_user_id: int,
    acting_user_id: int,
    team_id: str | None,
    team_role: str | None,
) -> DraftPermissions:
    if team_id is None:
        is_author = draft_author_user_id == acting_user_id
        return DraftPermissions(
            can_view=is_author,
            can_edit=is_author,
            can_delete=is_author,
            can_publish=is_author,
        )

    if team_role in {"owner", "editor"}:
        return _TEAM_DRAFT_MANAGER
    if team_role == "viewer":
        return _TEAM_DRAFT_VIEWER
    return _NO_DRAFT_ACCESS


def can_create_team_draft(team_role: str | None) -> bool:
    return team_role in {"owner", "editor"}
