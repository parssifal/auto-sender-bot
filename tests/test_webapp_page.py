from __future__ import annotations

from pathlib import Path

ADMIN_HTML = Path(__file__).resolve().parents[1] / "core" / "webapp_static" / "admin.html"


def test_admin_page_loads_telegram_sdk() -> None:
    html = ADMIN_HTML.read_text(encoding="utf-8")
    assert "telegram-web-app.js" in html


def test_admin_page_fetches_stats_endpoint() -> None:
    html = ADMIN_HTML.read_text(encoding="utf-8")
    assert "/api/stats" in html


def test_admin_page_is_self_contained_no_external_css_or_scripts() -> None:
    html = ADMIN_HTML.read_text(encoding="utf-8").lower()
    # The only allowed external resource is the Telegram SDK.
    assert "<link" not in html  # no external stylesheets
    assert html.count("http://") == 0  # no insecure external refs
    # Every <script src> must point at the Telegram SDK.
    import re

    for src in re.findall(r'<script[^>]*src="([^"]+)"', html):
        assert "telegram-web-app.js" in src


def test_admin_page_has_broadcast_card() -> None:
    html = ADMIN_HTML.read_text(encoding="utf-8")
    assert 'id="broadcast-card"' in html
    assert "Отправить всем" in html
    assert "/api/broadcast" in html


def test_admin_page_auto_refreshes_stats() -> None:
    html = ADMIN_HTML.read_text(encoding="utf-8")
    # Polling timer drives refreshStats(), the manual button shares the same fn.
    assert "setInterval" in html
    assert "refreshStats" in html
    # Pauses when the tab is hidden and refreshes on return.
    assert "visibilitychange" in html
    assert "visibilityState" in html
    # Overlap guard so ticks don't stack while a fetch is in flight.
    assert "inFlight" in html
    # "Last updated" indicator element.
    assert 'id="lastUpdated"' in html
