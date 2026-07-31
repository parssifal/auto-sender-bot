from __future__ import annotations

from dataclasses import dataclass

from core.services._shared import _resolve_team_id
from core.state import StateStore, Team, TeamInvite


@dataclass
class InvitePreparation:
    status: str                       # "ok" | "role_invalid" | "team_missing"
    invite: TeamInvite | None = None
    team: Team | None = None


async def prepare_team_invite(
    store: StateStore, *, owner_id: int, team_ref: str, role: str
) -> InvitePreparation:
    if role not in {"viewer", "editor"}:
        return InvitePreparation(status="role_invalid")
    owned_teams = await store.list_owned_teams(owner_id, limit=200)
    team_id = _resolve_team_id(owned_teams, team_ref)
    if team_id is None:
        return InvitePreparation(status="team_missing")
    try:
        invite = await store.create_team_invite(team_id, owner_id, role)
    except ValueError:
        return InvitePreparation(status="team_missing")
    team = next((t for t in owned_teams if t.id == team_id), None)
    if team is None:
        return InvitePreparation(status="team_missing")
    return InvitePreparation(status="ok", invite=invite, team=team)
