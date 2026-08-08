from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from aiogram.utils.web_app import check_webapp_signature, parse_webapp_init_data
from aiohttp import web

from core.services import admin_broadcast_svc
from core.state import ScheduledPostRow, StateStore
from core.utils import NonexistentLocalTimeError, parse_local_datetime, validate_schedule_time

logger = logging.getLogger(__name__)

DAY = 86_400
_STATIC_DIR = Path(__file__).parent / "webapp_static"

# --- Hardening defaults ---------------------------------------------------- #
# JSON payloads handled here are tiny; cap the raw request body well below
# aiohttp's 1 MiB default so an oversized POST cannot exhaust memory.
_DEFAULT_MAX_BODY_BYTES = 64 * 1024
# Per-remote request budget (defence-in-depth; a reverse proxy should also
# rate-limit, but the app must not fall over if that proxy is absent).
_DEFAULT_RATE_LIMIT_MAX = 240
_DEFAULT_RATE_LIMIT_WINDOW_S = 60.0
# A broadcast is a single Telegram message per recipient (max 4096 chars).
_DEFAULT_BROADCAST_TEXT_MAX = 4096
_ENTITIES_JSON_MAX = 32 * 1024


class _RateLimiter:
    """Fixed-window per-key request counter.

    Keyed on the client's remote address. The tracked-key set is bounded
    (LRU eviction) so a flood of distinct/spoofed keys cannot turn the limiter
    itself into a memory-exhaustion vector.
    """

    def __init__(
        self,
        *,
        max_requests: int,
        window_seconds: float,
        max_keys: int = 10_000,
    ) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._max_keys = max_keys
        # key -> (window_start, count)
        self._buckets: "OrderedDict[str, tuple[float, int]]" = OrderedDict()

    def allow(self, key: str, *, now: float) -> bool:
        window_start, count = self._buckets.get(key, (now, 0))
        if now - window_start >= self._window:
            window_start, count = now, 0

        count += 1
        self._buckets[key] = (window_start, count)
        self._buckets.move_to_end(key)
        while len(self._buckets) > self._max_keys:
            self._buckets.popitem(last=False)

        return count <= self._max

    def tracked_key_count(self) -> int:
        return len(self._buckets)


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_s: int = 3600,
    now: int | None = None,
) -> dict | None:
    """Validate a Telegram WebApp ``initData`` string.

    Returns the authenticated user as a dict (at least an ``id`` key) when the
    signature is valid, the payload contains a user, and ``auth_date`` is no
    older than ``max_age_s``. Returns ``None`` otherwise.
    """
    if not init_data or not check_webapp_signature(bot_token, init_data):
        return None
    try:
        data = parse_webapp_init_data(init_data)
    except (ValueError, KeyError):
        return None
    if data.user is None:
        return None

    auth_ts = int(data.auth_date.timestamp())
    current = int(time.time()) if now is None else now
    if current - auth_ts > max_age_s:
        return None

    return data.user.model_dump(exclude_none=True)


def _fill_daily(rows: list[tuple[str, int]], *, now: int, days: int = 30) -> list[list]:
    """Return a dense ``[[YYYY-MM-DD, count], ...]`` series for the last ``days``."""
    counts = {day: cnt for day, cnt in rows}
    start_day = datetime.fromtimestamp(now, tz=timezone.utc).date()
    series: list[list] = []
    for offset in range(days - 1, -1, -1):
        day = datetime.fromtimestamp(now - offset * DAY, tz=timezone.utc).date()
        key = day.isoformat()
        series.append([key, counts.get(key, 0)])
    _ = start_day
    return series


async def collect_admin_stats(store: StateStore, *, now: int | None = None) -> dict:
    current = int(time.time()) if now is None else now
    since_7d = current - 7 * DAY
    since_30d = current - 30 * DAY
    start_of_today = current - (current % DAY)

    posts_by_status = await store.count_posts_by_status()
    avg = await store.avg_destinations_per_user()

    return {
        "generated_at": current,
        "total_users": await store.count_users(),
        "avg_channels": round(avg, 2),
        "new_users_7d": await store.count_new_users(since_7d),
        "new_users_30d": await store.count_new_users(since_30d),
        "active_users_7d": await store.count_active_users(since_7d),
        "active_users_30d": await store.count_active_users(since_30d),
        "posts_by_status": posts_by_status,
        "queue": posts_by_status.get("pending", 0),
        "sent_today": await store.count_posts_sent_since(start_of_today),
        "sent_7d": await store.count_posts_sent_since(since_7d),
        "teams": await store.count_teams(),
        "drafts": await store.count_drafts(),
        "languages": [list(item) for item in await store.language_distribution()],
        "top_users": [list(item) for item in await store.top_active_users(limit=10, since_ts=since_30d)],
        "daily_new_users": _fill_daily(await store.daily_new_users(since_30d), now=current),
        "daily_posts_sent": _fill_daily(await store.daily_posts_sent(since_30d), now=current),
    }


@dataclass
class WebappServer:
    runner: web.AppRunner
    host: str
    port: int

    async def close(self) -> None:
        await self.runner.cleanup()

    def url(self, path: str = "/") -> str:
        return f"http://{self.host}:{self.port}{path}"


async def _post_to_json(store: StateStore, row: ScheduledPostRow) -> dict:
    """Map a pending one-off post to the Mini App JSON shape.

    No telegram/formatter import — the raw ``scheduled_at_utc`` epoch is returned
    and the browser formats local time from it (core must not import telegram).
    """
    title = await store.get_destination_title(row.chat_id) or str(row.chat_id)
    media = await store.get_post_media(row.id) if row.kind != "text" else []
    preview = (row.text or row.caption or "")[:120]
    return {
        "id": row.id,
        "destination_title": title,
        "scheduled_at_utc": row.scheduled_at_utc,
        "kind": row.kind,
        "media_count": len(media),
        "preview": preview,
    }


def _client_key(request: web.Request) -> str:
    """Rate-limit key: the real client behind our reverse proxy.

    Behind our own Caddy (deploy/Caddyfile) ``request.remote`` is always
    127.0.0.1, so keying on it puts every client in one bucket. Caddy appends
    the real peer as the LAST ``X-Forwarded-For`` hop; earlier hops are
    client-supplied and spoofable, so trust only the last one.
    """
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        last = xff.rsplit(",", 1)[-1].strip()
        if last:
            return last
    return request.remote or "unknown"


def _extract_init_data(request: web.Request) -> str | None:
    header = request.headers.get("Authorization")
    if not header:
        return None
    header = header.strip()
    # Telegram convention is "tma <initData>"; also accept the raw string.
    if header.lower().startswith("tma "):
        return header[4:].strip()
    return header


async def start_webapp_server(
    *,
    host: str,
    port: int,
    store: StateStore,
    bot_token: str,
    admin_ids: tuple[int, ...],
    bot: object | None = None,
    max_body_bytes: int = _DEFAULT_MAX_BODY_BYTES,
    rate_limit_max: int = _DEFAULT_RATE_LIMIT_MAX,
    rate_limit_window_s: float = _DEFAULT_RATE_LIMIT_WINDOW_S,
    broadcast_text_max: int = _DEFAULT_BROADCAST_TEXT_MAX,
) -> WebappServer:
    admin_set = set(admin_ids)
    rate_limiter = _RateLimiter(
        max_requests=rate_limit_max, window_seconds=rate_limit_window_s
    )

    # Read the static Mini App pages once at startup instead of hitting the
    # disk on every (unauthenticated) request.
    def _read_static(name: str) -> str:
        try:
            return (_STATIC_DIR / name).read_text(encoding="utf-8")
        except OSError:
            logger.warning("Static asset %s missing", name)
            return "<!doctype html><title>Not found</title>"

    admin_html = _read_static("admin.html")
    queue_html = _read_static("queue.html")

    @web.middleware
    async def _guard_mw(request: web.Request, handler):
        key = _client_key(request)
        if not rate_limiter.allow(key, now=time.monotonic()):
            return web.json_response({"error": "rate_limited"}, status=429)
        # Reject oversized bodies up front. aiohttp's client_max_size also guards
        # the streamed read, but handlers wrap request.json() in broad excepts
        # that would mask the 413 — checking Content-Length here keeps it clean.
        if (request.content_length or 0) > max_body_bytes:
            return web.json_response({"error": "payload_too_large"}, status=413)
        response = await handler(request)
        # Do not advertise the server implementation/version.
        response.headers["Server"] = "webapp"
        return response

    def _require_admin(request: web.Request) -> dict | None:
        init_data = _extract_init_data(request)
        if not init_data:
            return None
        user = validate_init_data(init_data, bot_token)
        if user is None or int(user.get("id", 0)) not in admin_set:
            return None
        return user

    def _require_user(request: web.Request) -> dict | None:
        init_data = _extract_init_data(request)
        if not init_data:
            return None
        return validate_init_data(init_data, bot_token)

    async def index(_request: web.Request) -> web.Response:
        return web.Response(text=admin_html, content_type="text/html")

    async def app_index(_request: web.Request) -> web.Response:
        return web.Response(text=queue_html, content_type="text/html")

    async def api_my_queue(request: web.Request) -> web.Response:
        user = _require_user(request)
        if user is None:
            return web.json_response({"error": "forbidden"}, status=403)
        user_id = int(user["id"])
        tz_name = await store.get_user_timezone(user_id) or "UTC"
        limit = 50
        rows = await store.list_editable_pending_posts(user_id, limit=limit + 1, offset=0)
        has_more = len(rows) > limit
        rows = rows[:limit]
        posts = [await _post_to_json(store, r) for r in rows]
        return web.json_response(
            {
                "tz": tz_name,
                "lang": await store.get_user_language(user_id),
                "is_admin": user_id in admin_set,
                "posts": posts,
                "has_more": has_more,
            }
        )

    async def api_my_recurring(request: web.Request) -> web.Response:
        user = _require_user(request)
        if user is None:
            return web.json_response({"error": "forbidden"}, status=403)
        user_id = int(user["id"])
        tz_name = await store.get_user_timezone(user_id) or "UTC"
        summaries = await store.list_user_recurring_summaries(user_id, offset=0, limit=50)
        patterns = [
            {
                "id": s.pattern.id,
                "destination_title": s.destination_title,
                "interval_type": s.pattern.interval_type,
                "time_of_day_minutes": s.pattern.time_of_day_minutes,
                "next_scheduled_at_utc": s.next_scheduled_at_utc,
            }
            for s in summaries
        ]
        return web.json_response(
            {"tz": tz_name, "lang": await store.get_user_language(user_id), "patterns": patterns}
        )

    async def api_my_reschedule(request: web.Request) -> web.Response:
        user = _require_user(request)
        if user is None:
            return web.json_response({"error": "forbidden"}, status=403)
        user_id = int(user["id"])
        post_id = request.match_info["id"]
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "bad_request"}, status=400)
        raw = str(payload.get("local_datetime") or "").strip()
        tz_name = await store.get_user_timezone(user_id) or "UTC"
        try:
            parsed = parse_local_datetime(raw, tz_name)
        except NonexistentLocalTimeError:
            return web.json_response({"error": "datetime_dst_gap"}, status=400)
        except (ValueError, KeyError):
            return web.json_response({"error": "bad_datetime"}, status=400)
        check = validate_schedule_time(parsed.utc_epoch)
        if not check.is_valid:
            return web.json_response({"error": check.error_key}, status=400)
        ok = await store.update_editable_post_time(post_id, user_id, scheduled_at_utc=parsed.utc_epoch)
        if not ok:
            return web.json_response({"error": "not_found"}, status=404)
        return web.json_response({"ok": True, "scheduled_at_utc": parsed.utc_epoch})

    async def api_my_cancel(request: web.Request) -> web.Response:
        user = _require_user(request)
        if user is None:
            return web.json_response({"error": "forbidden"}, status=403)
        ok = await store.cancel_post(int(user["id"]), request.match_info["id"])
        if not ok:
            return web.json_response({"error": "not_found"}, status=404)
        return web.json_response({"ok": True})

    async def api_my_recurring_cancel(request: web.Request) -> web.Response:
        user = _require_user(request)
        if user is None:
            return web.json_response({"error": "forbidden"}, status=403)
        ok = await store.cancel_recurring_pattern(int(user["id"]), request.match_info["id"])
        if not ok:
            return web.json_response({"error": "not_found"}, status=404)
        return web.json_response({"ok": True})

    async def api_stats(request: web.Request) -> web.Response:
        if _require_admin(request) is None:
            return web.json_response({"error": "forbidden"}, status=403)
        return web.json_response(await collect_admin_stats(store))

    async def api_user(request: web.Request) -> web.Response:
        if _require_admin(request) is None:
            return web.json_response({"error": "forbidden"}, status=403)
        try:
            user_id = int(request.match_info["id"])
        except (KeyError, ValueError):
            return web.json_response({"error": "bad_request"}, status=400)
        profile = await store.get_user_profile(user_id)
        if profile is None:
            return web.json_response({"error": "not_found"}, status=404)
        return web.json_response(profile)

    async def api_users(request: web.Request) -> web.Response:
        if _require_admin(request) is None:
            return web.json_response({"error": "forbidden"}, status=403)
        return web.json_response({"users": await store.list_users()})

    async def api_broadcast(request: web.Request) -> web.Response:
        if _require_admin(request) is None:
            return web.json_response({"error": "forbidden"}, status=403)
        if bot is None:
            return web.json_response({"error": "bot_unavailable"}, status=503)
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "bad_request"}, status=400)
        text = str(payload.get("text") or "").strip()
        if not text:
            return web.json_response({"error": "empty_text"}, status=400)
        if len(text) > broadcast_text_max:
            return web.json_response({"error": "text_too_long"}, status=400)
        entities_json = payload.get("entities_json")
        if isinstance(entities_json, str) and len(entities_json) > _ENTITIES_JSON_MAX:
            return web.json_response({"error": "entities_too_large"}, status=400)
        summary = await admin_broadcast_svc.broadcast_to_all(
            store, bot, text=text, entities_json=entities_json,
        )
        return web.json_response(summary)

    app = web.Application(client_max_size=max_body_bytes, middlewares=[_guard_mw])
    app.router.add_get("/", index)
    app.router.add_get("/api/stats", api_stats)
    app.router.add_get("/api/user/{id}", api_user)
    app.router.add_get("/api/users", api_users)
    app.router.add_post("/api/broadcast", api_broadcast)
    app.router.add_get("/app", app_index)
    app.router.add_get("/api/my/queue", api_my_queue)
    app.router.add_get("/api/my/recurring", api_my_recurring)
    app.router.add_post("/api/my/post/{id}/reschedule", api_my_reschedule)
    app.router.add_post("/api/my/post/{id}/cancel", api_my_cancel)
    app.router.add_post("/api/my/recurring/{id}/cancel", api_my_recurring_cancel)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()

    actual_host, actual_port = host, port
    if runner.addresses:
        first = runner.addresses[0]
        if isinstance(first, tuple) and len(first) >= 2:
            actual_host, actual_port = str(first[0]), int(first[1])

    logger.info("Webapp server started on %s:%s", actual_host, actual_port)
    return WebappServer(runner=runner, host=actual_host, port=actual_port)
