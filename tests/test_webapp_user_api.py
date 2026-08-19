from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest
import pytest_asyncio
from aiohttp import ClientSession, FormData

from core.db import open_db
from core.state import StateStore
from core.webapp import start_webapp_server

TOKEN = "123456:test-token"
USER_A = 111
USER_B = 222
CHAT_A = -3001


def _init_data(user_id: int, *, token: str = TOKEN, auth_date: int | None = None) -> str:
    user = {"id": user_id, "first_name": "U"}
    fields = {
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
        "query_id": "AAA",
        "user": json.dumps(user, separators=(",", ":")),
    }
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@pytest_asyncio.fixture
async def store() -> StateStore:
    conn = await open_db(":memory:")
    st = StateStore(conn)
    await st.migrate()
    for uid in (USER_A, USER_B):
        await st.ensure_user(uid)
        await st.set_user_language(uid, "ru")
    await st.upsert_destination(CHAT_A, "channel", "Channel A", None, "administrator", True)
    await st.link_user_destination(USER_A, CHAT_A, "link")
    await st.link_user_destination(USER_B, CHAT_A, "link")
    yield st
    await conn.close()


@pytest_asyncio.fixture
async def server(store: StateStore):
    srv = await start_webapp_server(
        host="127.0.0.1", port=0, store=store, bot_token=TOKEN, admin_ids=(999,),
    )
    yield srv
    await srv.close()


async def _mk_post(store: StateStore, user_id: int, *, when_offset: int = 3600) -> str:
    at = int(time.time()) + when_offset
    return await store.create_scheduled_text_post(user_id, CHAT_A, at, "hello", None)


async def _mk_recurring(store: StateStore, user_id: int) -> str:
    at = int(time.time()) + 3600
    return await store.create_recurring_pattern(
        user_id=user_id, chat_id=CHAT_A, interval_type="daily",
        time_of_day_minutes=540, timezone="UTC", start_at_utc=at,
    )


# --- Task 1: queue ---


@pytest.mark.asyncio
async def test_my_queue_requires_auth(server):
    async with ClientSession() as s:
        async with s.get(server.url("/api/my/queue")) as r:
            assert r.status == 403


@pytest.mark.asyncio
async def test_my_queue_bad_signature_forbidden(server):
    bad = _init_data(USER_A, token="999:wrong")
    async with ClientSession() as s:
        async with s.get(server.url("/api/my/queue"), headers={"Authorization": bad}) as r:
            assert r.status == 403


@pytest.mark.asyncio
async def test_my_queue_rejects_initdata_older_than_600s(server):
    # The caller enforces a 600s effective TTL on initData.
    stale = _init_data(USER_A, auth_date=int(time.time()) - 700)
    async with ClientSession() as s:
        async with s.get(server.url("/api/my/queue"),
                         headers={"Authorization": stale}) as r:
            assert r.status == 403


@pytest.mark.asyncio
async def test_my_queue_accepts_initdata_within_600s(server):
    fresh = _init_data(USER_A, auth_date=int(time.time()) - 100)
    async with ClientSession() as s:
        async with s.get(server.url("/api/my/queue"),
                         headers={"Authorization": fresh}) as r:
            assert r.status == 200


@pytest.mark.asyncio
async def test_my_queue_returns_only_callers_posts(server, store):
    pa = await _mk_post(store, USER_A)
    await _mk_post(store, USER_B)
    async with ClientSession() as s:
        async with s.get(server.url("/api/my/queue"),
                         headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 200
            body = await r.json()
    ids = [p["id"] for p in body["posts"]]
    assert ids == [pa]
    assert body["posts"][0]["destination_title"] == "Channel A"
    assert body["posts"][0]["kind"] == "text"


@pytest.mark.asyncio
async def test_my_queue_signals_truncation(server, store):
    # 51 posts > the 50 limit: the payload must flag that more exist.
    now = int(time.time())
    for i in range(51):
        await store.create_scheduled_text_post(USER_A, CHAT_A, now + 3600 + i, "hi", None)
    async with ClientSession() as s:
        async with s.get(server.url("/api/my/queue"),
                         headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 200
            body = await r.json()
    assert len(body["posts"]) == 50
    assert body["has_more"] is True


@pytest.mark.asyncio
async def test_my_queue_no_truncation_flag_when_under_limit(server, store):
    await _mk_post(store, USER_A)
    async with ClientSession() as s:
        async with s.get(server.url("/api/my/queue"),
                         headers={"Authorization": _init_data(USER_A)}) as r:
            body = await r.json()
    assert body["has_more"] is False


# --- Task 2: recurring ---


@pytest.mark.asyncio
async def test_my_recurring_requires_auth(server):
    async with ClientSession() as s:
        async with s.get(server.url("/api/my/recurring")) as r:
            assert r.status == 403


@pytest.mark.asyncio
async def test_my_recurring_returns_only_callers_patterns(server, store):
    pid = await _mk_recurring(store, USER_A)
    await _mk_recurring(store, USER_B)
    async with ClientSession() as s:
        async with s.get(server.url("/api/my/recurring"),
                         headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 200
            body = await r.json()
    ids = [p["id"] for p in body["patterns"]]
    assert ids == [pid]
    assert body["patterns"][0]["interval_type"] in {"daily", "weekly", "weekdays"}
    assert body["patterns"][0]["destination_title"] == "Channel A"


# --- Task 3: reschedule ---


@pytest.mark.asyncio
async def test_reschedule_happy(server, store):
    pid = await _mk_post(store, USER_A)
    dt = datetime.now(timezone.utc) + timedelta(days=2)
    local = dt.strftime("%d.%m.%Y %H:%M")
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/my/post/{pid}/reschedule"),
                          json={"local_datetime": local},
                          headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 200
    row = await store.get_scheduled_post(pid)
    assert row.scheduled_at_utc > int(time.time()) + 86400


@pytest.mark.asyncio
async def test_reschedule_past_time_400(server, store):
    pid = await _mk_post(store, USER_A)
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/my/post/{pid}/reschedule"),
                          json={"local_datetime": "01.01.2000 10:00"},
                          headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 400


@pytest.mark.asyncio
async def test_reschedule_unparseable_400(server, store):
    pid = await _mk_post(store, USER_A)
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/my/post/{pid}/reschedule"),
                          json={"local_datetime": "not-a-date"},
                          headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 400


@pytest.mark.asyncio
async def test_reschedule_dst_gap_400(server, store):
    # T-07 (Mini App surface): 02:30 on 2026-03-29 does not exist in Europe/Berlin.
    await store.set_user_timezone(USER_A, "Europe/Berlin")
    pid = await _mk_post(store, USER_A)
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/my/post/{pid}/reschedule"),
                          json={"local_datetime": "29.03.2026 02:30"},
                          headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 400
            body = await r.json()
            assert body["error"] == "datetime_dst_gap"


@pytest.mark.asyncio
async def test_reschedule_not_owned_404(server, store):
    pid = await _mk_post(store, USER_B)
    local = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%d.%m.%Y %H:%M")
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/my/post/{pid}/reschedule"),
                          json={"local_datetime": local},
                          headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 404


# --- Task 4: cancel post ---


@pytest.mark.asyncio
async def test_cancel_happy(server, store):
    pid = await _mk_post(store, USER_A)
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/my/post/{pid}/cancel"),
                          headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 200
    row = await store.get_scheduled_post(pid)
    assert row.status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_not_owned_404(server, store):
    pid = await _mk_post(store, USER_B)
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/my/post/{pid}/cancel"),
                          headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 404


# --- Task 5: cancel recurring ---


@pytest.mark.asyncio
async def test_recurring_cancel_happy(server, store):
    pid = await _mk_recurring(store, USER_A)
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/my/recurring/{pid}/cancel"),
                          headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 200
    active = await store.list_user_recurring_summaries(USER_A, offset=0, limit=50)
    assert all(s.pattern.id != pid for s in active)


@pytest.mark.asyncio
async def test_recurring_cancel_not_owned_404(server, store):
    pid = await _mk_recurring(store, USER_B)
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/my/recurring/{pid}/cancel"),
                          headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 404


# --- Task 6: /app serves html ---


@pytest.mark.asyncio
async def test_app_serves_html(server):
    async with ClientSession() as s:
        async with s.get(server.url("/app")) as r:
            assert r.status == 200
            assert r.content_type == "text/html"
            body = await r.text()
    assert "<html" in body.lower()


@pytest.mark.asyncio
async def test_my_queue_reports_admin_flag(store):
    # The queue payload carries an is_admin flag so the page can reveal the
    # admin-panel link only for admins. The caller id is derived server-side.
    async def _is_admin_for(admin_ids: tuple[int, ...]) -> bool:
        srv = await start_webapp_server(
            host="127.0.0.1", port=0, store=store, bot_token=TOKEN, admin_ids=admin_ids
        )
        try:
            async with ClientSession() as s:
                async with s.get(
                    srv.url("/api/my/queue"), headers={"Authorization": _init_data(USER_A)}
                ) as r:
                    assert r.status == 200
                    body = await r.json()
        finally:
            await srv.close()
        return body["is_admin"]

    assert await _is_admin_for((999,)) is False
    assert await _is_admin_for((USER_A,)) is True


# --- Task: admin grant plan (Phase 1) ---

ADMIN = 999


@pytest.mark.asyncio
async def test_set_plan_requires_admin(server):
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/user/{USER_A}/plan"),
                          json={"plan": "pro"},
                          headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 403


@pytest.mark.asyncio
async def test_set_plan_grants_indefinite(server, store):
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/user/{USER_A}/plan"),
                          json={"plan": "premium"},
                          headers={"Authorization": _init_data(ADMIN)}) as r:
            assert r.status == 200
            body = await r.json()
    assert body["plan"] == "premium"
    assert body["plan_expires_at"] is None
    assert await store.get_user_plan(USER_A) == "premium"


@pytest.mark.asyncio
async def test_set_plan_with_days_sets_expiry(server, store):
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/user/{USER_A}/plan"),
                          json={"plan": "pro", "days": 30},
                          headers={"Authorization": _init_data(ADMIN)}) as r:
            assert r.status == 200
            body = await r.json()
    assert body["plan_expires_at"] is not None
    assert body["plan_expires_at"] > int(time.time())
    assert await store.get_user_plan(USER_A) == "pro"


@pytest.mark.asyncio
async def test_set_plan_rejects_bad_plan(server):
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/user/{USER_A}/plan"),
                          json={"plan": "platinum"},
                          headers={"Authorization": _init_data(ADMIN)}) as r:
            assert r.status == 400
            body = await r.json()
    assert body["error"] == "bad_plan"


@pytest.mark.asyncio
async def test_set_plan_unknown_user_404(server):
    async with ClientSession() as s:
        async with s.post(server.url("/api/user/424242/plan"),
                          json={"plan": "pro"},
                          headers={"Authorization": _init_data(ADMIN)}) as r:
            assert r.status == 404


@pytest.mark.asyncio
async def test_user_profile_includes_plan(server, store):
    await store.set_user_plan(USER_A, "pro", None)
    async with ClientSession() as s:
        async with s.get(server.url(f"/api/user/{USER_A}"),
                         headers={"Authorization": _init_data(ADMIN)}) as r:
            assert r.status == 200
            body = await r.json()
    assert body["plan"] == "pro"
    assert body["plan_expires_at"] is None


# --- Task: post detail + media preview ---


class _FakeFile:
    def __init__(self, path: str) -> None:
        self.file_path = path


class _FakeBot:
    """Minimal duck-typed stand-in for aiogram's Bot media API."""

    def __init__(self) -> None:
        self.get_file_calls: list[str] = []

    async def get_file(self, file_id: str) -> _FakeFile:
        self.get_file_calls.append(file_id)
        return _FakeFile(f"photos/{file_id}.jpg")

    async def download_file(self, file_path: str):
        import io

        return io.BytesIO(b"BYTES-" + file_path.encode())


@pytest_asyncio.fixture
async def server_with_bot(store: StateStore):
    srv = await start_webapp_server(
        host="127.0.0.1", port=0, store=store, bot_token=TOKEN, admin_ids=(999,),
        bot=_FakeBot(),
    )
    yield srv
    await srv.close()


async def _mk_media_post(store: StateStore, user_id: int) -> str:
    at = int(time.time()) + 3600
    return await store.create_scheduled_media_post(
        user_id, CHAT_A, at,
        caption="look",
        caption_entities_json=json.dumps([{"type": "bold", "offset": 0, "length": 4}]),
        caption_above=None,
        media_items=[
            {"type": "photo", "file_id": "FID0"},
            {"type": "video", "file_id": "FID1"},
        ],
    )


@pytest.mark.asyncio
async def test_post_detail_requires_auth(server):
    async with ClientSession() as s:
        async with s.get(server.url("/api/my/post/whatever")) as r:
            assert r.status == 403


@pytest.mark.asyncio
async def test_post_detail_returns_content_and_media_for_owner(server, store):
    pid = await _mk_media_post(store, USER_A)
    async with ClientSession() as s:
        async with s.get(server.url(f"/api/my/post/{pid}"),
                         headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 200
            body = await r.json()
    assert body["kind"] == "media"
    assert body["caption"] == "look"
    assert body["caption_entities"] == [{"type": "bold", "offset": 0, "length": 4}]
    assert body["media"] == [
        {"idx": 0, "type": "photo"},
        {"idx": 1, "type": "video"},
    ]


@pytest.mark.asyncio
async def test_post_detail_hidden_from_non_owner(server, store):
    pid = await _mk_media_post(store, USER_A)
    async with ClientSession() as s:
        async with s.get(server.url(f"/api/my/post/{pid}"),
                         headers={"Authorization": _init_data(USER_B)}) as r:
            assert r.status == 404


@pytest.mark.asyncio
async def test_post_detail_unknown_id_404(server):
    async with ClientSession() as s:
        async with s.get(server.url("/api/my/post/nope"),
                         headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 404


@pytest.mark.asyncio
async def test_post_media_streams_bytes_for_owner(server_with_bot, store):
    pid = await _mk_media_post(store, USER_A)
    async with ClientSession() as s:
        async with s.get(server_with_bot.url(f"/api/my/post/{pid}/media/0"),
                         headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 200
            assert r.content_type == "image/jpeg"
            data = await r.read()
    assert data == b"BYTES-photos/FID0.jpg"


@pytest.mark.asyncio
async def test_post_media_hidden_from_non_owner(server_with_bot, store):
    pid = await _mk_media_post(store, USER_A)
    async with ClientSession() as s:
        async with s.get(server_with_bot.url(f"/api/my/post/{pid}/media/0"),
                         headers={"Authorization": _init_data(USER_B)}) as r:
            assert r.status == 404


@pytest.mark.asyncio
async def test_post_media_out_of_range_404(server_with_bot, store):
    pid = await _mk_media_post(store, USER_A)
    async with ClientSession() as s:
        async with s.get(server_with_bot.url(f"/api/my/post/{pid}/media/9"),
                         headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 404


@pytest.mark.asyncio
async def test_post_media_bad_index_400(server_with_bot, store):
    pid = await _mk_media_post(store, USER_A)
    async with ClientSession() as s:
        async with s.get(server_with_bot.url(f"/api/my/post/{pid}/media/x"),
                         headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 400


@pytest.mark.asyncio
async def test_post_media_without_bot_returns_503(server, store):
    pid = await _mk_media_post(store, USER_A)
    async with ClientSession() as s:
        async with s.get(server.url(f"/api/my/post/{pid}/media/0"),
                         headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 503


# --- Task: create a post from the Mini App ---


class _Member:
    def __init__(self, status: str, can_post: bool | None = True) -> None:
        self.status = status
        self.can_post_messages = can_post


class _Me:
    id = 42


class _SentMessage:
    def __init__(self, *, photo=None, video=None) -> None:
        self.message_id = 7
        self.photo = photo
        self.video = video


class _Sized:
    def __init__(self, file_id: str) -> None:
        self.file_id = file_id


class _ComposeBot(_FakeBot):
    """Fake bot covering the rights checks and the media staging round-trip."""

    def __init__(self, *, user_status: str = "administrator", bot_status: str = "administrator",
                 bot_can_post: bool | None = True) -> None:
        super().__init__()
        self.user_status = user_status
        self.bot_status = bot_status
        self.bot_can_post = bot_can_post
        self.sent: list[tuple[str, int, bytes]] = []
        self.deleted: list[tuple[int, int]] = []

    async def me(self) -> _Me:
        return _Me()

    async def get_chat_member(self, chat_id: int, user_id: int) -> _Member:
        if user_id == _Me.id:
            return _Member(self.bot_status, self.bot_can_post)
        return _Member(self.user_status)

    async def send_photo(self, chat_id: int, photo, disable_notification: bool = False) -> _SentMessage:
        self.sent.append(("photo", chat_id, photo.data))
        return _SentMessage(photo=[_Sized("SMALL"), _Sized("STAGED-PHOTO")])

    async def send_video(self, chat_id: int, video, disable_notification: bool = False) -> _SentMessage:
        self.sent.append(("video", chat_id, video.data))
        return _SentMessage(video=_Sized("STAGED-VIDEO"))

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        self.deleted.append((chat_id, message_id))


@pytest_asyncio.fixture
async def compose_bot() -> _ComposeBot:
    return _ComposeBot()


@pytest_asyncio.fixture
async def server_compose(store: StateStore, compose_bot: _ComposeBot):
    srv = await start_webapp_server(
        host="127.0.0.1", port=0, store=store, bot_token=TOKEN, admin_ids=(999,),
        bot=compose_bot,
    )
    yield srv
    await srv.close()


def _local_dt(offset_s: int) -> str:
    """A 'DD.MM.YYYY HH:MM' string in UTC (the fixture users have no timezone)."""
    when = datetime.now(timezone.utc) + timedelta(seconds=offset_s)
    return when.strftime("%d.%m.%Y %H:%M")


async def _create(server, user_id: int, **body):
    payload = {"chat_id": CHAT_A, "text": "hello", "local_datetime": _local_dt(7200), **body}
    async with ClientSession() as s:
        async with s.post(server.url("/api/my/post"),
                          headers={"Authorization": _init_data(user_id)},
                          json=payload) as r:
            return r.status, await r.json()


@pytest.mark.asyncio
async def test_destinations_requires_auth(server):
    async with ClientSession() as s:
        async with s.get(server.url("/api/my/destinations")) as r:
            assert r.status == 403


@pytest.mark.asyncio
async def test_destinations_lists_linked_with_post_flag(server, store):
    await store.upsert_destination(-3002, "channel", "Muted", None, "member", None)
    await store.link_user_destination(USER_A, -3002, "link")
    async with ClientSession() as s:
        async with s.get(server.url("/api/my/destinations"),
                         headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 200
            body = await r.json()
    by_id = {d["chat_id"]: d for d in body["destinations"]}
    assert by_id[CHAT_A]["can_post"] is True
    assert by_id[-3002]["can_post"] is False


@pytest.mark.asyncio
async def test_unlink_destination_removes_user_link_only(store):
    removed = await store.unlink_user_destination(USER_A, CHAT_A)

    assert removed is True
    assert await store.list_user_destinations(USER_A, 0, 10) == []
    assert await store.get_destination_title(CHAT_A) == "Channel A"
    assert [d.chat_id for d in await store.list_user_destinations(USER_B, 0, 10)] == [CHAT_A]


@pytest.mark.asyncio
async def test_create_post_requires_auth(server_compose):
    async with ClientSession() as s:
        async with s.post(server_compose.url("/api/my/post"), json={}) as r:
            assert r.status == 403


@pytest.mark.asyncio
async def test_create_text_post_happy(server_compose, store):
    status, body = await _create(server_compose, USER_A)
    assert status == 200, body
    row = await store.get_scheduled_post(body["id"])
    assert row.user_id == USER_A
    assert row.chat_id == CHAT_A
    assert row.kind == "text"
    assert row.text == "hello"
    assert row.status == "pending"
    assert row.scheduled_at_utc == body["scheduled_at_utc"]


@pytest.mark.asyncio
async def test_create_text_post_reuses_same_request_id(server_compose, store):
    request_id = "web-req-1"

    first_status, first_body = await _create(server_compose, USER_A, request_id=request_id)
    second_status, second_body = await _create(server_compose, USER_A, request_id=request_id)

    assert first_status == 200
    assert second_status == 200
    assert second_body["id"] == first_body["id"]
    posts = await store.list_pending_posts(USER_A, limit=10)
    assert [post.id for post in posts] == [first_body["id"]]


@pytest.mark.asyncio
async def test_create_post_rejects_unlinked_destination(server_compose):
    status, body = await _create(server_compose, USER_A, chat_id=-9999)
    assert status == 404
    assert body["error"] == "not_found"


@pytest.mark.asyncio
async def test_create_post_rejects_empty_content(server_compose):
    status, body = await _create(server_compose, USER_A, text="   ")
    assert status == 400
    assert body["error"] == "empty_text"


@pytest.mark.asyncio
async def test_create_post_rejects_past_time(server_compose):
    status, body = await _create(server_compose, USER_A, local_datetime=_local_dt(-3600))
    assert status == 400
    assert body["error"] == "datetime_future_required"


@pytest.mark.asyncio
async def test_create_post_rejects_unparseable_time(server_compose):
    status, body = await _create(server_compose, USER_A, local_datetime="tomorrow")
    assert status == 400
    assert body["error"] == "bad_datetime"


@pytest.mark.asyncio
async def test_create_post_rejects_overlong_text(server_compose):
    status, body = await _create(server_compose, USER_A, text="x" * 4097)
    assert status == 400
    assert body["error"] == "text_too_long"


@pytest.mark.asyncio
async def test_create_media_post_stores_items_and_caption(server_compose, store):
    status, body = await _create(
        server_compose, USER_A, text="look",
        media=[{"type": "photo", "file_id": "F1"}, {"type": "video", "file_id": "F2"}],
        caption_above=True,
    )
    assert status == 200, body
    row = await store.get_scheduled_post(body["id"])
    assert row.kind == "media"
    assert row.caption == "look"
    assert row.text is None
    assert bool(row.caption_above) is True
    assert await store.get_post_media(body["id"]) == [
        {"type": "photo", "file_id": "F1"},
        {"type": "video", "file_id": "F2"},
    ]


@pytest.mark.asyncio
async def test_create_post_rejects_unknown_media_type(server_compose):
    status, _ = await _create(server_compose, USER_A,
                              media=[{"type": "sticker", "file_id": "F1"}])
    assert status == 400


@pytest.mark.asyncio
async def test_create_post_rejects_too_many_media(server_compose):
    status, _ = await _create(
        server_compose, USER_A,
        media=[{"type": "photo", "file_id": f"F{i}"} for i in range(11)],
    )
    assert status == 400


@pytest.mark.asyncio
async def test_create_post_blocked_when_caller_lost_channel_admin(store):
    # The user_destinations link outlives the rights that created it.
    bot = _ComposeBot(user_status="member")
    srv = await start_webapp_server(
        host="127.0.0.1", port=0, store=store, bot_token=TOKEN, admin_ids=(999,), bot=bot,
    )
    try:
        status, body = await _create(srv, USER_A)
    finally:
        await srv.close()
    assert status == 400
    assert body["error"] == "rights_user_admin_required"
    assert await store.count_active_posts(USER_A) == 0


@pytest.mark.asyncio
async def test_create_post_blocked_when_bot_cannot_post(store):
    bot = _ComposeBot(bot_can_post=False)
    srv = await start_webapp_server(
        host="127.0.0.1", port=0, store=store, bot_token=TOKEN, admin_ids=(999,), bot=bot,
    )
    try:
        status, body = await _create(srv, USER_A)
    finally:
        await srv.close()
    assert status == 400
    assert body["error"] == "rights_bot_can_post_required"


@pytest.mark.asyncio
async def test_create_post_at_cap_returns_409(server_compose, store, monkeypatch):
    import core.limits as limits

    monkeypatch.setattr(limits, "MAX_ACTIVE_POSTS_PER_USER", 1)
    assert (await _create(server_compose, USER_A))[0] == 200
    status, body = await _create(server_compose, USER_A)
    assert status == 409
    assert body["error"] == "limit_posts"


@pytest.mark.asyncio
async def test_create_post_without_bot_returns_503(server):
    status, body = await _create(server, USER_A)
    assert status == 503
    assert body["error"] == "bot_unavailable"


async def _upload(server, user_id: int, *, data: bytes, content_type: str, name: str = "file"):
    form = FormData()
    form.add_field(name, data, filename="a.bin", content_type=content_type)
    async with ClientSession() as s:
        async with s.post(server.url("/api/my/media"),
                          headers={"Authorization": _init_data(user_id)},
                          data=form) as r:
            return r.status, await r.json()


@pytest.mark.asyncio
async def test_media_upload_requires_auth(server_compose):
    async with ClientSession() as s:
        async with s.post(server_compose.url("/api/my/media"), data=b"x") as r:
            assert r.status == 403


@pytest.mark.asyncio
async def test_media_upload_returns_file_id_and_clears_staging(server_compose, compose_bot):
    status, body = await _upload(server_compose, USER_A, data=b"JPEGBYTES", content_type="image/jpeg")
    assert status == 200, body
    # The largest PhotoSize is the one worth keeping.
    assert body == {"type": "photo", "file_id": "STAGED-PHOTO"}
    assert compose_bot.sent == [("photo", USER_A, b"JPEGBYTES")]
    assert compose_bot.deleted == [(USER_A, 7)]


@pytest.mark.asyncio
async def test_media_upload_accepts_raw_image_body(server_compose, compose_bot):
    async with ClientSession() as s:
        async with s.post(
            server_compose.url("/api/my/media"),
            headers={"Authorization": _init_data(USER_A), "Content-Type": "image/jpeg"},
            data=b"JPEGBYTES",
        ) as r:
            status, body = r.status, await r.json()

    assert status == 200, body
    assert body == {"type": "photo", "file_id": "STAGED-PHOTO"}
    assert compose_bot.sent == [("photo", USER_A, b"JPEGBYTES")]


@pytest.mark.asyncio
async def test_media_upload_handles_video(server_compose, compose_bot):
    status, body = await _upload(server_compose, USER_A, data=b"MP4", content_type="video/mp4")
    assert status == 200
    assert body == {"type": "video", "file_id": "STAGED-VIDEO"}


@pytest.mark.asyncio
async def test_media_upload_rejects_other_content_types(server_compose):
    status, body = await _upload(server_compose, USER_A, data=b"%PDF", content_type="application/pdf")
    assert status == 400
    assert body["error"] == "unsupported_media"


@pytest.mark.asyncio
async def test_media_upload_rejects_wrong_field_name(server_compose):
    status, _ = await _upload(server_compose, USER_A, data=b"x", content_type="image/jpeg", name="blob")
    assert status == 400


@pytest.mark.asyncio
async def test_media_upload_over_cap_returns_413(store, compose_bot):
    srv = await start_webapp_server(
        host="127.0.0.1", port=0, store=store, bot_token=TOKEN, admin_ids=(999,),
        bot=compose_bot, upload_max_bytes=1024,
    )
    try:
        status, body = await _upload(srv, USER_A, data=b"x" * 4096, content_type="image/jpeg")
    finally:
        await srv.close()
    assert status == 413
    assert body["error"] == "payload_too_large"
    assert compose_bot.sent == []


@pytest.mark.asyncio
async def test_media_upload_without_bot_returns_503(server):
    status, body = await _upload(server, USER_A, data=b"x", content_type="image/jpeg")
    assert status == 503


# --- Task: edit an existing post from the Mini App ---


async def _edit(server, user_id: int, post_id: str, **body):
    payload = {"chat_id": CHAT_A, "text": "hello", "local_datetime": _local_dt(7200), **body}
    async with ClientSession() as s:
        async with s.post(server.url(f"/api/my/post/{post_id}"),
                          headers={"Authorization": _init_data(user_id)},
                          json=payload) as r:
            return r.status, await r.json()


@pytest.mark.asyncio
async def test_edit_post_requires_auth(server_compose, store):
    post_id = await _mk_post(store, USER_A)
    async with ClientSession() as s:
        async with s.post(server_compose.url(f"/api/my/post/{post_id}"), json={}) as r:
            assert r.status == 403


@pytest.mark.asyncio
async def test_edit_post_rewrites_text_and_time(server_compose, store):
    post_id = await _mk_post(store, USER_A)
    when = _local_dt(9000)
    status, body = await _edit(server_compose, USER_A, post_id, text="rewritten", local_datetime=when)
    assert (status, body["ok"]) == (200, True)
    post = await store.get_scheduled_post(post_id)
    assert post.text == "rewritten"
    assert post.scheduled_at_utc == body["scheduled_at_utc"]


@pytest.mark.asyncio
async def test_edit_post_moves_to_another_linked_channel(server_compose, store):
    chat_b = -3009
    await store.upsert_destination(chat_b, "channel", "Channel B", None, "administrator", True)
    await store.link_user_destination(USER_A, chat_b, "link")
    post_id = await _mk_post(store, USER_A)
    status, _ = await _edit(server_compose, USER_A, post_id, chat_id=chat_b)
    assert status == 200
    post = await store.get_scheduled_post(post_id)
    assert post.chat_id == chat_b


@pytest.mark.asyncio
async def test_edit_post_rejects_unlinked_channel(server_compose, store):
    await store.upsert_destination(-3010, "channel", "Foreign", None, "administrator", True)
    post_id = await _mk_post(store, USER_A)
    status, body = await _edit(server_compose, USER_A, post_id, chat_id=-3010)
    assert (status, body["error"]) == (404, "not_found")
    post = await store.get_scheduled_post(post_id)
    assert post.chat_id == CHAT_A


@pytest.mark.asyncio
async def test_edit_post_of_another_user_is_not_found(server_compose, store):
    post_id = await _mk_post(store, USER_B)
    status, body = await _edit(server_compose, USER_A, post_id, text="stolen")
    assert (status, body["error"]) == (404, "not_found")
    post = await store.get_scheduled_post(post_id)
    assert post.text == "hello"


@pytest.mark.asyncio
async def test_edit_missing_post_is_not_found(server_compose):
    status, body = await _edit(server_compose, USER_A, "nope")
    assert (status, body["error"]) == (404, "not_found")


@pytest.mark.asyncio
async def test_edit_post_keeps_existing_media_by_index(server_compose, store):
    post_id = await _mk_media_post(store, USER_A)
    status, _ = await _edit(server_compose, USER_A, post_id, text="look",
                            media=[{"keep": 0}, {"keep": 1}])
    assert status == 200
    assert await store.get_post_media(post_id) == [
        {"type": "photo", "file_id": "FID0"},
        {"type": "video", "file_id": "FID1"},
    ]


@pytest.mark.asyncio
async def test_edit_post_reorders_existing_media(server_compose, store):
    """Array order is the new order; the indices still point at the old one."""
    post_id = await _mk_media_post(store, USER_A)
    status, _ = await _edit(server_compose, USER_A, post_id, text="look",
                            media=[{"keep": 1}, {"keep": 0}])
    assert status == 200
    assert await store.get_post_media(post_id) == [
        {"type": "video", "file_id": "FID1"},
        {"type": "photo", "file_id": "FID0"},
    ]


@pytest.mark.asyncio
async def test_edit_post_drops_media(server_compose, store):
    post_id = await _mk_media_post(store, USER_A)
    status, _ = await _edit(server_compose, USER_A, post_id, text="look", media=[{"keep": 1}])
    assert status == 200
    assert await store.get_post_media(post_id) == [{"type": "video", "file_id": "FID1"}]


@pytest.mark.asyncio
async def test_edit_post_appends_newly_uploaded_media(server_compose, store):
    post_id = await _mk_media_post(store, USER_A)
    status, _ = await _edit(server_compose, USER_A, post_id, text="look",
                            media=[{"keep": 0}, {"type": "photo", "file_id": "NEW"}])
    assert status == 200
    assert await store.get_post_media(post_id) == [
        {"type": "photo", "file_id": "FID0"},
        {"type": "photo", "file_id": "NEW"},
    ]


@pytest.mark.asyncio
async def test_edit_post_rejects_keep_index_out_of_range(server_compose, store):
    post_id = await _mk_media_post(store, USER_A)
    status, body = await _edit(server_compose, USER_A, post_id, text="look", media=[{"keep": 7}])
    assert (status, body["error"]) == (400, "bad_request")
    assert len(await store.get_post_media(post_id)) == 2


@pytest.mark.asyncio
async def test_create_post_rejects_keep_marker(server_compose):
    """Nothing exists to keep on the create route — a keep marker is a client bug."""
    status, body = await _create(server_compose, USER_A, media=[{"keep": 0}])
    assert (status, body["error"]) == (400, "bad_request")


@pytest.mark.asyncio
async def test_edit_post_dropping_all_media_turns_it_into_text(server_compose, store):
    post_id = await _mk_media_post(store, USER_A)
    status, _ = await _edit(server_compose, USER_A, post_id, text="just words", media=[])
    assert status == 200
    post = await store.get_scheduled_post(post_id)
    assert (post.kind, post.text, post.caption) == ("text", "just words", None)
    assert await store.get_post_media(post_id) == []


@pytest.mark.asyncio
async def test_edit_post_adding_media_turns_text_into_media(server_compose, store):
    post_id = await _mk_post(store, USER_A)
    status, _ = await _edit(server_compose, USER_A, post_id, text="now with art",
                            media=[{"type": "photo", "file_id": "NEW"}], caption_above=True)
    assert status == 200
    post = await store.get_scheduled_post(post_id)
    assert (post.kind, post.caption, post.text) == ("media", "now with art", None)
    assert bool(post.caption_above) is True
    assert await store.get_post_media(post_id) == [{"type": "photo", "file_id": "NEW"}]


@pytest.mark.asyncio
async def test_edit_post_keeps_formatting_when_text_is_untouched(server_compose, store):
    """Editing only the media must not strip entities the bot flow put there."""
    post_id = await _mk_media_post(store, USER_A)
    before = await store.get_scheduled_post(post_id)
    status, _ = await _edit(server_compose, USER_A, post_id, text="look", media=[{"keep": 0}])
    assert status == 200
    post = await store.get_scheduled_post(post_id)
    assert post.caption_entities_json == before.caption_entities_json


@pytest.mark.asyncio
async def test_edit_post_drops_formatting_when_text_changes(server_compose, store):
    post_id = await _mk_media_post(store, USER_A)
    status, _ = await _edit(server_compose, USER_A, post_id, text="new words", media=[{"keep": 0}])
    assert status == 200
    post = await store.get_scheduled_post(post_id)
    assert post.caption_entities_json is None


@pytest.mark.asyncio
async def test_edit_recurring_instance_is_not_found(server_compose, store):
    """Recurring instances are owned by their series, not editable one by one."""
    _, post_id = await store.create_recurring_series(
        user_id=USER_A, chat_id=CHAT_A, interval_type="daily",
        time_of_day_minutes=540, timezone="UTC", start_at_utc=int(time.time()) + 3600,
        kind="text", text="series post", entities_json=None,
    )
    status, body = await _edit(server_compose, USER_A, post_id, text="nope")
    assert (status, body["error"]) == (404, "not_found")


@pytest.mark.asyncio
async def test_edit_post_rejects_empty_content(server_compose, store):
    post_id = await _mk_post(store, USER_A)
    status, body = await _edit(server_compose, USER_A, post_id, text="   ", media=[])
    assert (status, body["error"]) == (400, "empty_text")


@pytest.mark.asyncio
async def test_edit_post_rejects_past_time(server_compose, store):
    post_id = await _mk_post(store, USER_A)
    status, body = await _edit(server_compose, USER_A, post_id, local_datetime=_local_dt(-3600))
    assert status == 400
    assert body["error"].startswith("datetime_")


@pytest.mark.asyncio
async def test_edit_post_blocked_when_bot_cannot_post(store):
    bot = _ComposeBot(bot_can_post=False)
    srv = await start_webapp_server(
        host="127.0.0.1", port=0, store=store, bot_token=TOKEN, admin_ids=(999,), bot=bot,
    )
    post_id = await _mk_post(store, USER_A)
    try:
        status, body = await _edit(srv, USER_A, post_id, text="rewritten")
    finally:
        await srv.close()
    assert status == 400
    assert body["error"].startswith("rights_")
    post = await store.get_scheduled_post(post_id)
    assert post.text == "hello"


@pytest.mark.asyncio
async def test_post_detail_exposes_fields_the_editor_prefills(server_with_bot, store):
    post_id = await _mk_media_post(store, USER_A)
    async with ClientSession() as s:
        async with s.get(server_with_bot.url(f"/api/my/post/{post_id}"),
                         headers={"Authorization": _init_data(USER_A)}) as r:
            assert r.status == 200
            body = await r.json()
    assert body["chat_id"] == CHAT_A
    assert body["caption_above"] is False
    # file_ids stay server-side: the editor refers to media by index instead.
    assert "FID0" not in json.dumps(body)
