from __future__ import annotations

from pathlib import Path

ADMIN_HTML = Path(__file__).resolve().parents[1] / "core" / "webapp_static" / "admin.html"
QUEUE_HTML = Path(__file__).resolve().parents[1] / "core" / "webapp_static" / "queue.html"


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


def test_admin_page_has_back_to_queue_link() -> None:
    html = ADMIN_HTML.read_text(encoding="utf-8")
    # A back-link returns to the user queue Mini App at /app.
    assert 'id="back-app"' in html
    assert '"/app"' in html


def test_queue_page_loads_telegram_sdk() -> None:
    # Without the Telegram SDK, window.Telegram.WebApp is undefined, initData
    # is empty, and the page falls back to demo mode instead of the real user.
    html = QUEUE_HTML.read_text(encoding="utf-8")
    assert "telegram-web-app.js" in html


def test_queue_page_keeps_auth_header_on_calls_with_own_headers() -> None:
    # Regression: `{headers:{...AUTH}, ...opts}` let opts.headers (set by the
    # reschedule POST for Content-Type) clobber Authorization → 403.
    html = QUEUE_HTML.read_text(encoding="utf-8")
    call = html.split("await fetch(path,")[1].split(");")[0]
    assert call.index("...opts") < call.index("headers:{...AUTH")


def test_queue_page_has_role_gated_admin_link() -> None:
    html = QUEUE_HTML.read_text(encoding="utf-8")
    # The admin-panel button exists but starts hidden…
    assert 'id="admin-link"' in html
    # …is only revealed when the queue payload flags the caller as admin…
    assert "q.is_admin" in html
    # …and navigates to the admin dashboard at "/".
    assert 'location.href = "/"' in html


def test_queue_page_has_post_preview() -> None:
    html = QUEUE_HTML.read_text(encoding="utf-8")
    # Clicking a post opens a preview sheet fed by the per-post detail endpoint.
    assert 'id="detail"' in html
    assert "/api/my/post/" in html
    # Photos are fetched as authorised blobs (the <img> tag cannot send the
    # initData header) and the object URLs are revoked to avoid leaks.
    assert "createObjectURL" in html
    assert "revokeObjectURL" in html


def test_queue_page_preview_images_fit_without_cropping() -> None:
    html = QUEUE_HTML.read_text(encoding="utf-8")
    # Vertical photos must be shown whole in the Mini App preview sheet.
    assert "object-fit:contain" in html


def test_queue_page_reuses_compose_sheet_for_editing() -> None:
    html = QUEUE_HTML.read_text(encoding="utf-8")
    # One sheet writes both a new and an existing post; the id decides the route.
    assert "let composeEditId" in html
    assert 'composeEditId ? `/api/my/post/${composeEditId}` : "/api/my/post"' in html
    # Entry points: the queue cards and the preview sheet.
    assert 'data-act="edit"' in html
    assert 'id="pv-edit"' in html


def test_queue_page_reorders_media_by_pointer_drag() -> None:
    html = QUEUE_HTML.read_text(encoding="utf-8")
    # HTML5 drag-and-drop is mouse-only by spec, so the Mini App (a mobile
    # WebView) must reorder through pointer events instead.
    assert "draggable" not in html
    assert "setPointerCapture" in html
    # The grip opts out of touch scrolling; the tile itself keeps panning the
    # strip, so dragging and scrolling never fight over the same gesture.
    assert "touch-action:none" in html
    # Every way a gesture can end resets the drag — an interrupted one would
    # otherwise leave dragFrom armed and silently stop thumbnail rendering.
    assert "lostpointercapture" in html


def test_queue_page_keeps_keyboard_path_for_reordering() -> None:
    html = QUEUE_HTML.read_text(encoding="utf-8")
    # Dragging is pointer-only; the same grip answers arrow keys so reordering
    # stays reachable without a pointer.
    assert '"ArrowLeft"' in html and '"ArrowRight"' in html
    assert "data-grip" in html


def test_queue_page_sends_existing_media_as_position_markers() -> None:
    html = QUEUE_HTML.read_text(encoding="utf-8")
    # Media already on the post travel back by index, so a Telegram file_id is
    # never handed to the browser to echo.
    assert "{ keep:m.keep }" in html
    assert "m.keep === undefined" in html
