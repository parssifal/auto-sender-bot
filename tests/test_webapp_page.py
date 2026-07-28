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
