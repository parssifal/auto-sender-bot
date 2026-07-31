from __future__ import annotations

from telegram.user_app import build_user_app_router


def test_user_app_router_builds_with_url():
    r = build_user_app_router(store=object(), webapp_url="https://example.org")
    assert r is not None
    assert r.name == "user_app"


def test_user_app_router_builds_without_url():
    r = build_user_app_router(store=object(), webapp_url=None)
    assert r is not None
