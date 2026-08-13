from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from aiogram.types import BufferedInputFile
from aiogram.utils.web_app import check_webapp_signature, parse_webapp_init_data
from aiohttp import web

import core.limits as limits
from core.limits import ResourceLimitError
from core.services import admin_broadcast_svc, rights_svc
from core.state import ScheduledPostRow, StateStore
from core.utils import NonexistentLocalTimeError, parse_local_datetime, validate_schedule_time

logger = logging.getLogger(__name__)

DAY = 86_400
_STATIC_DIR = Path(__file__).parent / "webapp_static"

# Content types for proxied Telegram media. Telegram photos are always JPEG;
# other kinds fall back to a generic binary type.
_MEDIA_CONTENT_TYPES = {
    "photo": "image/jpeg",
    "video": "video/mp4",
    "animation": "video/mp4",
    "audio": "audio/mpeg",
    "voice": "audio/ogg",
    "document": "application/octet-stream",
}

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
# Media staged for a Mini App post: one multipart file per request, relayed to
# Telegram. Bot API caps photo uploads at 10 MiB and other files at 50 MiB;
# 20 MiB keeps videos usable without letting one request eat the process.
_DEFAULT_UPLOAD_MAX_BYTES = 20 * 1024 * 1024
_UPLOAD_PATH = "/api/my/media"
# Telegram media groups hold at most 10 items (same cap as the bot flow).
_MAX_MEDIA_PER_POST = 10
# A scheduled text post is one Telegram message.
_POST_TEXT_MAX = 4096
# Effective TTL for a Telegram WebApp initData payload. Telegram itself does not
# expire it, so we bound replay of a leaked payload to a short window.
_INIT_DATA_MAX_AGE_S = 600


def _upload_kind(content_type: str | None) -> str | None:
    ctype = (content_type or "").lower()
    if ctype.startswith("image/"):
        return "photo"
    if ctype.startswith("video/"):
        return "video"
    return None


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
    series: list[list] = []
    for offset in range(days - 1, -1, -1):
        day = datetime.fromtimestamp(now - offset * DAY, tz=timezone.utc).date()
        key = day.isoformat()
        series.append([key, counts.get(key, 0)])
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
    upload_max_bytes: int = _DEFAULT_UPLOAD_MAX_BYTES,
) -> WebappServer:
    if not bot_token:
        raise ValueError("bot_token must be non-empty")
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
        # Media staging is the one route that legitimately carries megabytes.
        body_cap = upload_max_bytes if request.path == _UPLOAD_PATH else max_body_bytes
        if (request.content_length or 0) > body_cap:
            return web.json_response({"error": "payload_too_large"}, status=413)
        response = await handler(request)
        # Do not advertise the server implementation/version.
        response.headers["Server"] = "webapp"
        return response

    def _require_admin(request: web.Request) -> dict | None:
        init_data = _extract_init_data(request)
        if not init_data:
            return None
        user = validate_init_data(init_data, bot_token, max_age_s=_INIT_DATA_MAX_AGE_S)
        if user is None or int(user.get("id", 0)) not in admin_set:
            return None
        return user

    def _require_user(request: web.Request) -> dict | None:
        init_data = _extract_init_data(request)
        if not init_data:
            return None
        return validate_init_data(init_data, bot_token, max_age_s=_INIT_DATA_MAX_AGE_S)

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

    def _parse_entities(raw: str | None) -> list | None:
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None

    async def api_my_post_detail(request: web.Request) -> web.Response:
        user = _require_user(request)
        if user is None:
            return web.json_response({"error": "forbidden"}, status=403)
        user_id = int(user["id"])
        post_id = request.match_info["id"]
        row = await store.get_scheduled_post(post_id)
        # Ownership is the trust boundary: a missing post and someone else's
        # post are indistinguishable to the caller (both 404, no enumeration).
        if row is None or row.user_id != user_id:
            return web.json_response({"error": "not_found"}, status=404)
        media = await store.get_post_media(post_id) if row.kind == "media" else []
        return web.json_response(
            {
                "id": row.id,
                "kind": row.kind,
                "chat_id": row.chat_id,
                "scheduled_at_utc": row.scheduled_at_utc,
                "caption_above": bool(row.caption_above),
                "text": row.text,
                "entities": _parse_entities(row.entities_json),
                "caption": row.caption,
                "caption_entities": _parse_entities(row.caption_entities_json),
                "media": [{"idx": i, "type": m["type"]} for i, m in enumerate(media)],
            }
        )

    async def api_my_post_media(request: web.Request) -> web.Response:
        user = _require_user(request)
        if user is None:
            return web.json_response({"error": "forbidden"}, status=403)
        user_id = int(user["id"])
        post_id = request.match_info["id"]
        try:
            idx = int(request.match_info["idx"])
        except (KeyError, ValueError):
            return web.json_response({"error": "bad_request"}, status=400)
        row = await store.get_scheduled_post(post_id)
        if row is None or row.user_id != user_id:
            return web.json_response({"error": "not_found"}, status=404)
        media = await store.get_post_media(post_id)
        if idx < 0 or idx >= len(media):
            return web.json_response({"error": "not_found"}, status=404)
        if bot is None:
            return web.json_response({"error": "bot_unavailable"}, status=503)
        item = media[idx]
        # Download server-side so the bot token never reaches the client; the
        # WebView fetches this via an authorised request and object-URLs it.
        try:
            file = await bot.get_file(item["file_id"])
            data = await bot.download_file(file.file_path)
        except Exception:
            logger.warning("media download failed for post %s idx %s", post_id, idx)
            return web.json_response({"error": "media_unavailable"}, status=502)
        if hasattr(data, "read"):
            data = data.read()
        ctype = _MEDIA_CONTENT_TYPES.get(item["type"], "application/octet-stream")
        return web.Response(
            body=data,
            content_type=ctype,
            headers={"Cache-Control": "private, max-age=3600, immutable"},
        )

    async def api_my_destinations(request: web.Request) -> web.Response:
        user = _require_user(request)
        if user is None:
            return web.json_response({"error": "forbidden"}, status=403)
        rows = await store.list_user_destinations(
            int(user["id"]), 0, limits.MAX_DESTINATIONS_PER_USER
        )
        return web.json_response(
            {
                "destinations": [
                    {
                        "chat_id": d.chat_id,
                        "title": d.title,
                        "username": d.username,
                        # Cached from my_chat_member updates: a UI hint only. The
                        # authoritative check runs live when the post is created.
                        "can_post": d.bot_status == "administrator" and d.bot_can_post is not False,
                    }
                    for d in rows
                ]
            }
        )

    async def api_my_media(request: web.Request) -> web.Response:
        """Stage an uploaded file as a Telegram ``file_id``.

        A browser cannot produce a ``file_id``, and the Bot API has no
        upload-without-send call — so the file is sent to the caller's own chat
        with the bot and the staging message is deleted right after.
        """
        user = _require_user(request)
        if user is None:
            return web.json_response({"error": "forbidden"}, status=403)
        if bot is None:
            return web.json_response({"error": "bot_unavailable"}, status=503)
        user_id = int(user["id"])

        async def _read_upload(read_chunk) -> tuple[bytes | None, web.Response | None]:
            # Read bounded: a chunked upload carries no Content-Length, so the
            # middleware's cap cannot see it and this loop is the only guard.
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = await read_chunk(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > upload_max_bytes:
                    return None, web.json_response({"error": "payload_too_large"}, status=413)
                chunks.append(chunk)
            if not total:
                return None, web.json_response({"error": "bad_request"}, status=400)
            return b"".join(chunks), None

        field = None
        try:
            reader = await request.multipart()
            field = await reader.next()
        except Exception:
            kind = _upload_kind(request.content_type)
            if kind is None:
                return web.json_response({"error": "bad_request"}, status=400)
        else:
            if field is None or field.name != "file":
                return web.json_response({"error": "bad_request"}, status=400)
            kind = _upload_kind(field.headers.get("Content-Type"))
            if kind is None:
                return web.json_response({"error": "unsupported_media"}, status=400)

        if field is None:
            body, bad = await _read_upload(request.content.read)
            filename = "upload.jpg" if kind == "photo" else "upload.mp4"
        else:
            body, bad = await _read_upload(field.read_chunk)
            filename = field.filename or ("upload.jpg" if kind == "photo" else "upload.mp4")
        if bad is not None:
            return bad

        upload = BufferedInputFile(
            body or b"",
            filename=filename,
        )
        try:
            if kind == "photo":
                sent = await bot.send_photo(chat_id=user_id, photo=upload, disable_notification=True)
                file_id = sent.photo[-1].file_id if sent.photo else None
            else:
                sent = await bot.send_video(chat_id=user_id, video=upload, disable_notification=True)
                file_id = sent.video.file_id if sent.video else None
        except Exception:
            logger.warning("media staging failed for user %s (%s)", user_id, kind)
            return web.json_response({"error": "upload_failed"}, status=502)

        try:
            await bot.delete_message(chat_id=user_id, message_id=sent.message_id)
        except Exception:
            # The file_id stays valid; a leftover message in the user's own chat
            # is cosmetic and must not fail the upload.
            logger.info("could not delete staging message for user %s", user_id)

        if not file_id:
            return web.json_response({"error": "upload_failed"}, status=502)
        return web.json_response({"type": kind, "file_id": file_id})

    async def _validate_post_payload(
        request: web.Request,
        user_id: int,
        *,
        existing_media: list[dict[str, str]] | None,
    ) -> tuple[dict | None, web.Response | None]:
        """Validate a create/edit post body: destination, media, text, time, rights.

        Both routes describe the post's whole desired state, so the rules live
        here once instead of drifting apart in two handlers.

        ``existing_media`` is the post's current media on the edit route, which
        lets the client keep an item by position (``{"keep": 2}``) — Telegram
        file_ids then never have to travel to the browser. ``None`` means the
        create route, where there is nothing to keep.
        """
        try:
            payload = await request.json()
        except Exception:
            return None, web.json_response({"error": "bad_request"}, status=400)
        if not isinstance(payload, dict):
            return None, web.json_response({"error": "bad_request"}, status=400)

        try:
            chat_id = int(payload["chat_id"])
        except (KeyError, TypeError, ValueError):
            return None, web.json_response({"error": "bad_request"}, status=400)
        # Trust boundary: the caller may only target destinations linked to them.
        linked = await store.list_user_destinations(
            user_id, 0, limits.MAX_DESTINATIONS_PER_USER
        )
        if chat_id not in {d.chat_id for d in linked}:
            return None, web.json_response({"error": "not_found"}, status=404)

        raw_media = payload.get("media") or []
        if not isinstance(raw_media, list) or len(raw_media) > _MAX_MEDIA_PER_POST:
            return None, web.json_response({"error": "bad_request"}, status=400)
        media_items: list[dict[str, str]] = []
        for item in raw_media:
            if not isinstance(item, dict):
                return None, web.json_response({"error": "bad_request"}, status=400)
            if "keep" in item:
                try:
                    idx = int(item["keep"])
                except (TypeError, ValueError):
                    return None, web.json_response({"error": "bad_request"}, status=400)
                if existing_media is None or not 0 <= idx < len(existing_media):
                    return None, web.json_response({"error": "bad_request"}, status=400)
                kept = existing_media[idx]
                media_items.append({"type": kept["type"], "file_id": kept["file_id"]})
                continue
            m_type = str(item.get("type") or "")
            file_id = str(item.get("file_id") or "")
            if m_type not in {"photo", "video"} or not file_id:
                return None, web.json_response({"error": "bad_request"}, status=400)
            media_items.append({"type": m_type, "file_id": file_id})

        text = str(payload.get("text") or "").strip()
        if not media_items and not text:
            return None, web.json_response({"error": "empty_text"}, status=400)
        if len(text) > _POST_TEXT_MAX:
            return None, web.json_response({"error": "text_too_long"}, status=400)

        tz_name = await store.get_user_timezone(user_id) or "UTC"
        try:
            parsed = parse_local_datetime(str(payload.get("local_datetime") or "").strip(), tz_name)
        except NonexistentLocalTimeError:
            return None, web.json_response({"error": "datetime_dst_gap"}, status=400)
        except (ValueError, KeyError):
            return None, web.json_response({"error": "bad_datetime"}, status=400)
        check = validate_schedule_time(parsed.utc_epoch)
        if not check.is_valid:
            return None, web.json_response({"error": check.error_key}, status=400)

        # Same rights gate as the bot flow: the destination link outlives the
        # rights that created it, so both sides must be re-checked live.
        if bot is None:
            return None, web.json_response({"error": "bot_unavailable"}, status=503)
        err = await rights_svc.check_user_is_admin(bot, chat_id, user_id)
        if err is None:
            err = await rights_svc.check_bot_can_post(bot, chat_id)
        if err is not None:
            return None, web.json_response({"error": err.key}, status=400)

        return (
            {
                "chat_id": chat_id,
                "text": text,
                "media_items": media_items,
                "caption_above": bool(payload.get("caption_above")),
                "scheduled_at_utc": parsed.utc_epoch,
            },
            None,
        )

    async def api_my_create_post(request: web.Request) -> web.Response:
        user = _require_user(request)
        if user is None:
            return web.json_response({"error": "forbidden"}, status=403)
        user_id = int(user["id"])
        data, bad = await _validate_post_payload(request, user_id, existing_media=None)
        if data is None:
            return bad

        try:
            if data["media_items"]:
                post_id = await store.create_scheduled_media_post(
                    user_id=user_id,
                    chat_id=data["chat_id"],
                    scheduled_at_utc=data["scheduled_at_utc"],
                    caption=data["text"] or None,
                    caption_entities_json=None,
                    caption_above=data["caption_above"],
                    media_items=data["media_items"],
                )
            else:
                post_id = await store.create_scheduled_text_post(
                    user_id=user_id,
                    chat_id=data["chat_id"],
                    scheduled_at_utc=data["scheduled_at_utc"],
                    text=data["text"],
                    entities_json=None,
                )
        except ResourceLimitError as exc:
            return web.json_response({"error": f"limit_{exc.resource}"}, status=409)
        return web.json_response(
            {"ok": True, "id": post_id, "scheduled_at_utc": data["scheduled_at_utc"]}
        )

    async def api_my_edit_post(request: web.Request) -> web.Response:
        """Replace a pending post's destination, content and time in one call."""
        user = _require_user(request)
        if user is None:
            return web.json_response({"error": "forbidden"}, status=403)
        user_id = int(user["id"])
        post_id = request.match_info["id"]
        row = await store.get_scheduled_post(post_id)
        # Ownership is the trust boundary: a missing post and someone else's post
        # are indistinguishable to the caller (both 404, no enumeration).
        if row is None or row.user_id != user_id:
            return web.json_response({"error": "not_found"}, status=404)
        current_media = await store.get_post_media(post_id) if row.kind == "media" else []

        data, bad = await _validate_post_payload(
            request, user_id, existing_media=current_media
        )
        if data is None:
            return bad

        # The Mini App has no formatting editor, so a rewritten text arrives
        # plain and loses its entities (same as creating a post here). An
        # untouched text keeps the entities it already has — editing only the
        # media must not strip formatting the post got from the bot flow.
        current_text = (row.caption if row.kind == "media" else row.text) or ""
        current_entities = (
            row.caption_entities_json if row.kind == "media" else row.entities_json
        )
        entities = current_entities if data["text"] == current_text else None

        updates: dict[str, object] = {
            "scheduled_at_utc": data["scheduled_at_utc"],
            "chat_id": data["chat_id"],
        }
        if data["media_items"]:
            updates |= {
                "kind": "media",
                "caption": data["text"] or None,
                "caption_entities_json": entities,
                "caption_above": data["caption_above"],
                "media_items": data["media_items"],
            }
        else:
            updates |= {
                "kind": "text",
                "text": data["text"],
                "entities_json": entities,
            }

        # False means the post stopped being editable between the read above and
        # the write: sent, cancelled, or adopted by a recurring series.
        if not await store.update_scheduled_post(post_id, user_id, updates):
            return web.json_response({"error": "not_found"}, status=404)
        return web.json_response(
            {"ok": True, "id": post_id, "scheduled_at_utc": data["scheduled_at_utc"]}
        )

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
        summary = await admin_broadcast_svc.broadcast_to_all(store, bot, text=text)
        return web.json_response(summary)

    app = web.Application(client_max_size=max_body_bytes, middlewares=[_guard_mw])
    app.router.add_get("/", index)
    app.router.add_get("/api/stats", api_stats)
    app.router.add_get("/api/user/{id}", api_user)
    app.router.add_get("/api/users", api_users)
    app.router.add_post("/api/broadcast", api_broadcast)
    app.router.add_get("/app", app_index)
    app.router.add_get("/api/my/queue", api_my_queue)
    app.router.add_get("/api/my/destinations", api_my_destinations)
    app.router.add_post("/api/my/post", api_my_create_post)
    app.router.add_post(_UPLOAD_PATH, api_my_media)
    app.router.add_get("/api/my/recurring", api_my_recurring)
    app.router.add_get("/api/my/post/{id}", api_my_post_detail)
    app.router.add_post("/api/my/post/{id}", api_my_edit_post)
    app.router.add_get("/api/my/post/{id}/media/{idx}", api_my_post_media)
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
