from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from aiogram.utils.web_app import check_webapp_signature, parse_webapp_init_data
from aiohttp import web

from core.state import StateStore

logger = logging.getLogger(__name__)

DAY = 86_400
_STATIC_DIR = Path(__file__).parent / "webapp_static"


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
) -> WebappServer:
    admin_set = set(admin_ids)

    def _require_admin(request: web.Request) -> dict | None:
        init_data = _extract_init_data(request)
        if not init_data:
            return None
        user = validate_init_data(init_data, bot_token)
        if user is None or int(user.get("id", 0)) not in admin_set:
            return None
        return user

    async def index(_request: web.Request) -> web.Response:
        html_path = _STATIC_DIR / "admin.html"
        return web.Response(text=html_path.read_text(encoding="utf-8"), content_type="text/html")

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

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/stats", api_stats)
    app.router.add_get("/api/user/{id}", api_user)
    app.router.add_get("/api/users", api_users)

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
