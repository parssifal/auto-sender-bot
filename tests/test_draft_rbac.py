from core.rbac import (
    can_create_team_draft,
    can_delete_draft,
    can_edit_draft,
    can_publish_draft,
    can_view_draft,
    resolve_draft_permissions,
)


def test_resolve_draft_permissions_for_personal_author() -> None:
    permissions = resolve_draft_permissions(
        draft_author_user_id=123,
        acting_user_id=123,
        team_id=None,
        team_role=None,
    )

    assert permissions.can_view is True
    assert permissions.can_edit is True
    assert permissions.can_delete is True
    assert permissions.can_publish is True


def test_resolve_draft_permissions_for_personal_non_author() -> None:
    permissions = resolve_draft_permissions(
        draft_author_user_id=123,
        acting_user_id=456,
        team_id=None,
        team_role=None,
    )

    assert permissions.can_view is False
    assert permissions.can_edit is False
    assert permissions.can_delete is False
    assert permissions.can_publish is False


def test_resolve_draft_permissions_for_team_roles() -> None:
    owner_permissions = resolve_draft_permissions(
        draft_author_user_id=123,
        acting_user_id=456,
        team_id="team-1",
        team_role="owner",
    )
    editor_permissions = resolve_draft_permissions(
        draft_author_user_id=123,
        acting_user_id=456,
        team_id="team-1",
        team_role="editor",
    )
    viewer_permissions = resolve_draft_permissions(
        draft_author_user_id=123,
        acting_user_id=456,
        team_id="team-1",
        team_role="viewer",
    )
    outsider_permissions = resolve_draft_permissions(
        draft_author_user_id=123,
        acting_user_id=456,
        team_id="team-1",
        team_role=None,
    )

    assert owner_permissions.can_publish is True
    assert editor_permissions.can_edit is True
    assert editor_permissions.can_delete is True
    assert viewer_permissions.can_view is True
    assert viewer_permissions.can_edit is False
    assert viewer_permissions.can_delete is False
    assert viewer_permissions.can_publish is False
    assert outsider_permissions.can_view is False


def test_convenience_wrappers_match_matrix() -> None:
    kwargs = {
        "draft_author_user_id": 123,
        "acting_user_id": 456,
        "team_id": "team-1",
        "team_role": "editor",
    }

    assert can_view_draft(**kwargs) is True
    assert can_edit_draft(**kwargs) is True
    assert can_delete_draft(**kwargs) is True
    assert can_publish_draft(**kwargs) is True
    assert can_create_team_draft("owner") is True
    assert can_create_team_draft("editor") is True
    assert can_create_team_draft("viewer") is False
    assert can_create_team_draft(None) is False
