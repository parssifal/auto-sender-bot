import pytest
from core.services import team_svc


@pytest.mark.asyncio
async def test_prepare_team_invite_rejects_bad_role(store):
    await store.ensure_user(1)
    result = await team_svc.prepare_team_invite(store, owner_id=1, team_ref="abcd", role="admin")
    assert result.status == "role_invalid"


@pytest.mark.asyncio
async def test_prepare_team_invite_missing_team(store):
    await store.ensure_user(1)
    result = await team_svc.prepare_team_invite(store, owner_id=1, team_ref="zzzz", role="viewer")
    assert result.status == "team_missing"


@pytest.mark.asyncio
async def test_prepare_team_invite_ok_returns_invite_and_team(store):
    await store.ensure_user(1)
    team_id = await store.create_team(1, "Team X")
    # _resolve_team_id exact-matches the full team_id first, so passing the
    # full id (already lowercase uuid4 hex) reliably resolves regardless of
    # whether any prefix collisions exist among owned teams.
    result = await team_svc.prepare_team_invite(store, owner_id=1, team_ref=team_id, role="editor")
    assert result.status == "ok"
    assert result.team is not None and result.invite is not None
    # The invite must carry the requested role and be tied to the resolved team.
    assert result.invite.role == "editor"
    assert result.invite.team_id == team_id
    assert result.team.id == team_id
