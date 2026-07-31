from __future__ import annotations

import re
from dataclasses import dataclass
import json
from typing import Any

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import Update

from core.db import open_db
from core.state import StateStore
from telegram.i18n import tr
from telegram.handlers.states import DraftStates
from telegram.router import build_router

USER_ID = 1001
PRIVATE_CHAT_ID = USER_ID
ALT_USER_ID = 2002
OUTSIDER_USER_ID = 3003
DESTINATION_CHAT_ID = -2001
BOT_ID = 42


class FakeBot(Bot):
    def __init__(self) -> None:
        super().__init__(f"{BOT_ID}:TEST")
        self.calls: list[Any] = []

    async def __call__(self, method: Any, request_timeout: int | None = None) -> Any:
        self.calls.append(method)
        return True

    async def me(self):
        return type("Me", (), {"id": BOT_ID, "username": "test_bot"})()

    async def get_chat_member(self, **kwargs):
        user_id = kwargs["user_id"]
        if user_id == BOT_ID:
            return type("Member", (), {"status": "administrator", "can_post_messages": True})()
        return type("Member", (), {"status": "administrator"})()


@dataclass
class DraftFlowHarness:
    bot: FakeBot
    dispatcher: Dispatcher
    store: StateStore
    storage_key: StorageKey
    conn: Any
    owner_team_id: str
    viewer_team_id: str

    async def feed_message(
        self,
        text: str,
        *,
        update_id: int,
        message_id: int,
        user_id: int = USER_ID,
        chat_id: int | None = None,
    ) -> None:
        effective_chat_id = user_id if chat_id is None else chat_id
        payload: dict[str, Any] = {
            "update_id": update_id,
            "message": {
                "message_id": message_id,
                "date": 1_700_000_000,
                "chat": {"id": effective_chat_id, "type": "private"},
                "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                "text": text,
            },
        }
        if text.startswith("/"):
            payload["message"]["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        await self.dispatcher.feed_update(self.bot, Update.model_validate(payload))

    async def feed_callback(
        self,
        data: str,
        *,
        update_id: int,
        message_id: int,
        user_id: int = USER_ID,
        chat_id: int | None = None,
    ) -> None:
        effective_chat_id = user_id if chat_id is None else chat_id
        payload = {
            "update_id": update_id,
            "callback_query": {
                "id": f"q{update_id}",
                "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                "chat_instance": "ci",
                "data": data,
                "message": {
                    "message_id": message_id,
                    "date": 1_700_000_000,
                    "chat": {"id": effective_chat_id, "type": "private"},
                    "from": {"id": BOT_ID, "is_bot": True, "first_name": "Bot"},
                    "text": "stub",
                },
            },
        }
        await self.dispatcher.feed_update(self.bot, Update.model_validate(payload))

    async def feed_photo(
        self,
        file_id: str,
        *,
        update_id: int,
        message_id: int,
        caption: str | None = None,
        user_id: int = USER_ID,
        chat_id: int | None = None,
    ) -> None:
        effective_chat_id = user_id if chat_id is None else chat_id
        payload: dict[str, Any] = {
            "update_id": update_id,
            "message": {
                "message_id": message_id,
                "date": 1_700_000_000,
                "chat": {"id": effective_chat_id, "type": "private"},
                "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                "photo": [
                    {
                        "file_id": file_id,
                        "file_unique_id": f"{file_id}_unique",
                        "width": 100,
                        "height": 100,
                    }
                ],
            },
        }
        if caption is not None:
            payload["message"]["caption"] = caption
        await self.dispatcher.feed_update(self.bot, Update.model_validate(payload))

    async def get_state(self) -> str | None:
        return await self.dispatcher.storage.get_state(self.storage_key)

    async def get_data(self) -> dict[str, Any]:
        return await self.dispatcher.storage.get_data(self.storage_key)

    def last_call(self) -> Any:
        return self.bot.calls[-1]


def _callback_data(call: SendMessage | EditMessageText) -> list[str]:
    return [button.callback_data for row in call.reply_markup.inline_keyboard for button in row]


def _short_id(value: str) -> str:
    return value[:8]


@pytest_asyncio.fixture
async def draft_flow() -> DraftFlowHarness:
    conn = await open_db(":memory:")
    store = StateStore(conn)
    await store.migrate()
    await store.ensure_user(USER_ID)
    await store.set_user_language(USER_ID, "ru")
    await store.set_user_timezone(USER_ID, "Europe/Moscow")
    await store.ensure_user(ALT_USER_ID)
    await store.set_user_language(ALT_USER_ID, "ru")
    await store.set_user_timezone(ALT_USER_ID, "Europe/Moscow")
    await store.ensure_user(OUTSIDER_USER_ID)
    await store.set_user_language(OUTSIDER_USER_ID, "ru")
    await store.set_user_timezone(OUTSIDER_USER_ID, "Europe/Moscow")
    await store.upsert_destination(
        DESTINATION_CHAT_ID,
        "channel",
        "Test channel",
        "test_channel",
        "administrator",
        True,
    )
    await store.link_user_destination(USER_ID, DESTINATION_CHAT_ID, "link")

    owner_team_id = await store.create_team(USER_ID, "Owners")
    viewer_team_id = await store.create_team(ALT_USER_ID, "Shared")
    await store.upsert_team_member(viewer_team_id, USER_ID, "viewer")

    bot = FakeBot()
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(build_router(store))

    yield DraftFlowHarness(
        bot=bot,
        dispatcher=dispatcher,
        store=store,
        storage_key=StorageKey(bot_id=BOT_ID, chat_id=PRIVATE_CHAT_ID, user_id=USER_ID),
        conn=conn,
        owner_team_id=owner_team_id,
        viewer_team_id=viewer_team_id,
    )

    await conn.close()
    await bot.session.close()


@pytest.mark.asyncio
async def test_drafts_command_shows_empty_state_with_filters(draft_flow: DraftFlowHarness) -> None:
    await draft_flow.feed_message("/drafts", update_id=1, message_id=10)

    call = draft_flow.last_call()
    assert isinstance(call, SendMessage)
    assert call.text == tr("ru", "draft_list_empty", scope=tr("ru", "draft_filter_all"))
    assert call.reply_markup.inline_keyboard[0][0].text == f"[{tr('ru', 'draft_filter_all')}]"
    assert call.reply_markup.inline_keyboard[0][1].callback_data == "dscope:mine"
    assert call.reply_markup.inline_keyboard[0][2].callback_data == "dscope:team"


@pytest.mark.asyncio
async def test_start_without_invite_keeps_default_onboarding(draft_flow: DraftFlowHarness) -> None:
    await draft_flow.feed_message("/start", update_id=1, message_id=10)

    call = draft_flow.last_call()
    assert isinstance(call, SendMessage)
    assert call.text == tr("ru", "start_message")


@pytest.mark.asyncio
async def test_draft_create_personal_text_flow_saves_draft(draft_flow: DraftFlowHarness) -> None:
    await draft_flow.feed_message("/draft_create", update_id=1, message_id=10)

    assert await draft_flow.get_state() == DraftStates.choosing_destination.state
    choose_call = draft_flow.last_call()
    assert isinstance(choose_call, SendMessage)
    assert choose_call.text == tr("ru", "choose_destination")
    assert f"ddsel:{DESTINATION_CHAT_ID}" in _callback_data(choose_call)

    await draft_flow.feed_callback(f"ddsel:{DESTINATION_CHAT_ID}", update_id=2, message_id=50)

    assert await draft_flow.get_state() == DraftStates.collecting_post.state
    json.dumps(await draft_flow.get_data())
    collect_call = draft_flow.last_call()
    assert isinstance(collect_call, SendMessage)
    assert collect_call.text == tr("ru", "schedule_post_prompt")

    await draft_flow.feed_message("Личный текстовый черновик", update_id=3, message_id=11)
    await draft_flow.feed_callback("smedia:done", update_id=4, message_id=51)

    assert await draft_flow.get_state() == DraftStates.choosing_scope.state
    scope_call = draft_flow.last_call()
    assert isinstance(scope_call, SendMessage)
    assert scope_call.text == tr("ru", "draft_create_scope_prompt")
    callbacks = _callback_data(scope_call)
    assert "dcscope:personal" in callbacks
    assert f"dcscope:team:{draft_flow.owner_team_id}" in callbacks
    assert f"dcscope:team:{draft_flow.viewer_team_id}" not in callbacks

    await draft_flow.feed_callback("dcscope:personal", update_id=5, message_id=52)

    assert await draft_flow.get_state() is None
    final_call = draft_flow.last_call()
    assert isinstance(final_call, SendMessage)
    assert "draft=" in final_call.text
    assert tr("ru", "draft_location_personal") in final_call.text

    drafts = await draft_flow.store.list_drafts(USER_ID, scope="all")
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.team_id is None
    assert draft.author_user_id == USER_ID
    assert draft.chat_id == DESTINATION_CHAT_ID
    assert draft.kind == "text"
    assert draft.text == "Личный текстовый черновик"


@pytest.mark.asyncio
async def test_draft_create_team_media_flow_saves_team_draft(draft_flow: DraftFlowHarness) -> None:
    await draft_flow.feed_message("/draft_create", update_id=1, message_id=10)
    await draft_flow.feed_callback(f"ddsel:{DESTINATION_CHAT_ID}", update_id=2, message_id=50)

    assert await draft_flow.get_state() == DraftStates.collecting_post.state
    await draft_flow.feed_photo("photo-1", update_id=3, message_id=11, caption="Командный медиа черновик")
    await draft_flow.feed_callback("smedia:done", update_id=4, message_id=51)

    scope_call = draft_flow.last_call()
    assert isinstance(scope_call, SendMessage)
    callbacks = _callback_data(scope_call)
    assert f"dcscope:team:{draft_flow.owner_team_id}" in callbacks
    assert f"dcscope:team:{draft_flow.viewer_team_id}" not in callbacks

    await draft_flow.feed_callback(f"dcscope:team:{draft_flow.owner_team_id}", update_id=5, message_id=52)

    assert await draft_flow.get_state() is None
    final_call = draft_flow.last_call()
    assert isinstance(final_call, SendMessage)
    assert "draft=" in final_call.text
    assert "Owners" in final_call.text

    drafts = await draft_flow.store.list_drafts(USER_ID, scope="team")
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.team_id == draft_flow.owner_team_id
    assert draft.kind == "media"
    assert draft.caption == "Командный медиа черновик"
    assert await draft_flow.store.get_draft_media(draft.id) == [{"type": "photo", "file_id": "photo-1"}]


@pytest.mark.asyncio
async def test_team_create_command_persists_team(draft_flow: DraftFlowHarness) -> None:
    await draft_flow.feed_message("/team_create Editorial board", update_id=1, message_id=10)

    call = draft_flow.last_call()
    assert isinstance(call, SendMessage)
    assert "Команда создана." in call.text
    assert "Editorial board" in call.text

    owned_teams = await draft_flow.store.list_owned_teams(USER_ID)
    assert [team.name for team in owned_teams[:2]] == ["Editorial board", "Owners"]


@pytest.mark.asyncio
async def test_team_invite_accept_flow_adds_member_and_lists_roles(draft_flow: DraftFlowHarness) -> None:
    await draft_flow.feed_message(f"/team_invite {draft_flow.owner_team_id[:8]} editor", update_id=1, message_id=10)

    invite_call = draft_flow.last_call()
    assert isinstance(invite_call, SendMessage)
    match = re.search(r"ti_([0-9a-f]{32})", invite_call.text)
    assert match is not None

    token = match.group(1)
    await draft_flow.feed_message(f"/start ti_{token}", update_id=2, message_id=11, user_id=ALT_USER_ID)

    accept_call = draft_flow.last_call()
    assert isinstance(accept_call, SendMessage)
    assert "Вы вступили в команду." in accept_call.text
    assert "редактор" in accept_call.text
    assert await draft_flow.store.get_team_member_role(draft_flow.owner_team_id, ALT_USER_ID) == "editor"

    await draft_flow.feed_message(f"/team_members {draft_flow.owner_team_id[:8]}", update_id=3, message_id=12)

    members_call = draft_flow.last_call()
    assert isinstance(members_call, SendMessage)
    assert "владелец: user 1001" in members_call.text
    assert "редактор: user 2002" in members_call.text


@pytest.mark.asyncio
async def test_team_members_without_id_lists_accessible_teams_and_invite_requires_owner(draft_flow: DraftFlowHarness) -> None:
    await draft_flow.feed_message("/team_members", update_id=1, message_id=10)

    list_call = draft_flow.last_call()
    assert isinstance(list_call, SendMessage)
    assert _short_id(draft_flow.owner_team_id) in list_call.text
    assert _short_id(draft_flow.viewer_team_id) in list_call.text

    await draft_flow.feed_message(f"/team_invite {draft_flow.viewer_team_id[:8]}", update_id=2, message_id=11)

    denied_call = draft_flow.last_call()
    assert isinstance(denied_call, SendMessage)
    assert denied_call.text == tr("ru", "team_missing")


@pytest.mark.asyncio
async def test_draft_edit_via_detail_button_updates_personal_draft_in_place(draft_flow: DraftFlowHarness) -> None:
    draft_id = await draft_flow.store.create_draft(
        author_user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        kind="text",
        text="Старый личный черновик",
    )

    await draft_flow.feed_message("/drafts", update_id=1, message_id=10)
    await draft_flow.feed_callback(f"dopen:all:0:{draft_id}", update_id=2, message_id=50)
    await draft_flow.feed_callback(f"dact:edit:{draft_id}", update_id=3, message_id=50)

    assert await draft_flow.get_state() == DraftStates.editing_post.state
    prompt_call = draft_flow.last_call()
    assert isinstance(prompt_call, SendMessage)
    assert _short_id(draft_id) in prompt_call.text

    await draft_flow.feed_message("Новый личный черновик", update_id=4, message_id=11)
    await draft_flow.feed_callback("smedia:done", update_id=5, message_id=51)

    assert await draft_flow.get_state() is None
    final_call = draft_flow.last_call()
    assert isinstance(final_call, SendMessage)
    assert "draft=" in final_call.text

    updated = await draft_flow.store.get_draft(draft_id)
    assert updated is not None
    assert updated.id == draft_id
    assert updated.kind == "text"
    assert updated.text == "Новый личный черновик"
    assert await draft_flow.store.get_draft_media(draft_id) == []


@pytest.mark.asyncio
async def test_draft_edit_command_updates_team_draft_in_place(draft_flow: DraftFlowHarness) -> None:
    draft_id = await draft_flow.store.create_draft(
        author_user_id=USER_ID,
        team_id=draft_flow.owner_team_id,
        chat_id=DESTINATION_CHAT_ID,
        kind="text",
        text="Старый командный черновик",
    )

    await draft_flow.feed_message(f"/draft_edit {draft_id[:8]}", update_id=1, message_id=10)

    assert await draft_flow.get_state() == DraftStates.editing_post.state
    prompt_call = draft_flow.last_call()
    assert isinstance(prompt_call, SendMessage)
    assert "Owners" in prompt_call.text

    await draft_flow.feed_photo("photo-edit-1", update_id=2, message_id=11, caption="Новый командный медиа черновик")
    await draft_flow.feed_callback("smedia:done", update_id=3, message_id=51)

    assert await draft_flow.get_state() is None
    final_call = draft_flow.last_call()
    assert isinstance(final_call, SendMessage)
    assert "Owners" in final_call.text

    updated = await draft_flow.store.get_draft(draft_id)
    assert updated is not None
    assert updated.id == draft_id
    assert updated.team_id == draft_flow.owner_team_id
    assert updated.kind == "media"
    assert updated.caption == "Новый командный медиа черновик"
    assert updated.text is None
    assert await draft_flow.store.get_draft_media(draft_id) == [{"type": "photo", "file_id": "photo-edit-1"}]


@pytest.mark.asyncio
async def test_draft_edit_command_rejects_viewer_role(draft_flow: DraftFlowHarness) -> None:
    draft_id = await draft_flow.store.create_draft(
        author_user_id=ALT_USER_ID,
        team_id=draft_flow.viewer_team_id,
        chat_id=DESTINATION_CHAT_ID,
        kind="text",
        text="Недоступный для редактирования",
    )

    await draft_flow.feed_message(f"/draft_edit {draft_id[:8]}", update_id=1, message_id=10)

    assert await draft_flow.get_state() is None
    call = draft_flow.last_call()
    assert isinstance(call, SendMessage)
    assert call.text == tr("ru", "draft_missing")

    unchanged = await draft_flow.store.get_draft(draft_id)
    assert unchanged is not None
    assert unchanged.text == "Недоступный для редактирования"


@pytest.mark.asyncio
async def test_draft_delete_via_detail_confirmation_updates_list(draft_flow: DraftFlowHarness) -> None:
    kept_id = await draft_flow.store.create_draft(
        author_user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        kind="text",
        text="Оставшийся черновик",
    )
    deleted_id = await draft_flow.store.create_draft(
        author_user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        kind="media",
        caption="Удаляемый черновик",
        media_items=[{"type": "photo", "file_id": "photo-delete-1"}],
    )

    await draft_flow.feed_message("/drafts", update_id=1, message_id=10)
    await draft_flow.feed_callback(f"dopen:all:0:{deleted_id}", update_id=2, message_id=50)
    await draft_flow.feed_callback(f"ddelask:all:0:{deleted_id}", update_id=3, message_id=50)

    confirm_call = draft_flow.last_call()
    assert isinstance(confirm_call, EditMessageText)
    assert tr("ru", "draft_delete_confirm", draft_id=_short_id(deleted_id), location="", where="", kind="").startswith(
        "Удалить"
    )
    callbacks = _callback_data(confirm_call)
    assert f"ddelyes:all:0:{deleted_id}" in callbacks
    assert f"dopen:all:0:{deleted_id}" in callbacks

    await draft_flow.feed_callback(f"ddelyes:all:0:{deleted_id}", update_id=4, message_id=50)

    final_call = draft_flow.last_call()
    assert isinstance(final_call, EditMessageText)
    assert _short_id(kept_id) in final_call.text
    assert _short_id(deleted_id) not in final_call.text
    assert await draft_flow.store.get_draft(deleted_id) is None
    assert await draft_flow.store.get_draft_media(deleted_id) == []


@pytest.mark.asyncio
async def test_draft_delete_command_confirms_and_removes_draft(draft_flow: DraftFlowHarness) -> None:
    draft_id = await draft_flow.store.create_draft(
        author_user_id=USER_ID,
        team_id=draft_flow.owner_team_id,
        chat_id=DESTINATION_CHAT_ID,
        kind="text",
        text="Черновик для удаления",
    )

    await draft_flow.feed_message(f"/draft_delete {draft_id[:8]}", update_id=1, message_id=10)

    confirm_call = draft_flow.last_call()
    assert isinstance(confirm_call, SendMessage)
    assert _short_id(draft_id) in confirm_call.text
    assert f"ddelcmd:{draft_id}" in _callback_data(confirm_call)

    await draft_flow.feed_callback(f"ddelcmd:{draft_id}", update_id=2, message_id=50)

    final_call = draft_flow.last_call()
    assert isinstance(final_call, EditMessageText)
    assert final_call.text == tr("ru", "draft_delete_ok", draft_id=_short_id(draft_id))
    assert await draft_flow.store.get_draft(draft_id) is None


@pytest.mark.asyncio
async def test_draft_delete_command_rejects_viewer_role(draft_flow: DraftFlowHarness) -> None:
    draft_id = await draft_flow.store.create_draft(
        author_user_id=ALT_USER_ID,
        team_id=draft_flow.viewer_team_id,
        chat_id=DESTINATION_CHAT_ID,
        kind="text",
        text="Удалять нельзя",
    )

    await draft_flow.feed_message(f"/draft_delete {draft_id[:8]}", update_id=1, message_id=10)

    call = draft_flow.last_call()
    assert isinstance(call, SendMessage)
    assert call.text == tr("ru", "draft_missing")
    assert await draft_flow.store.get_draft(draft_id) is not None


@pytest.mark.asyncio
async def test_draft_publish_via_detail_button_creates_scheduled_post_and_keeps_draft(draft_flow: DraftFlowHarness) -> None:
    draft_id = await draft_flow.store.create_draft(
        author_user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        kind="text",
        text="Черновик для публикации",
    )

    await draft_flow.feed_message("/drafts", update_id=1, message_id=10)
    await draft_flow.feed_callback(f"dopen:all:0:{draft_id}", update_id=2, message_id=50)
    await draft_flow.feed_callback(f"dact:publish:{draft_id}", update_id=3, message_id=50)

    assert await draft_flow.get_state() == DraftStates.entering_datetime.state
    prompt_call = draft_flow.last_call()
    assert isinstance(prompt_call, SendMessage)
    assert _short_id(draft_id) in prompt_call.text

    await draft_flow.feed_callback("tp:quick:next_monday", update_id=4, message_id=51)

    assert await draft_flow.get_state() == DraftStates.confirming.state
    confirm_call = draft_flow.last_call()
    assert isinstance(confirm_call, SendMessage)
    assert "Test channel" in confirm_call.text

    await draft_flow.feed_callback("sconf:yes", update_id=5, message_id=52)

    assert await draft_flow.get_state() is None
    final_call = draft_flow.last_call()
    assert isinstance(final_call, SendMessage)
    assert _short_id(draft_id) in final_call.text

    pending_posts = await draft_flow.store.list_pending_posts(USER_ID, limit=10)
    assert len(pending_posts) == 1
    post = pending_posts[0]
    assert post.chat_id == DESTINATION_CHAT_ID
    assert post.kind == "text"
    assert post.text == "Черновик для публикации"
    assert await draft_flow.store.get_draft(draft_id) is not None


@pytest.mark.asyncio
async def test_draft_post_command_creates_scheduled_post_from_team_media_draft(draft_flow: DraftFlowHarness) -> None:
    draft_id = await draft_flow.store.create_draft(
        author_user_id=USER_ID,
        team_id=draft_flow.owner_team_id,
        chat_id=DESTINATION_CHAT_ID,
        kind="media",
        caption="Командный draft publish",
        media_items=[{"type": "photo", "file_id": "photo-publish-1"}],
    )

    await draft_flow.feed_message(f"/draft_post {draft_id[:8]}", update_id=1, message_id=10)

    assert await draft_flow.get_state() == DraftStates.entering_datetime.state
    prompt_call = draft_flow.last_call()
    assert isinstance(prompt_call, SendMessage)
    assert _short_id(draft_id) in prompt_call.text

    await draft_flow.feed_callback("tp:quick:next_monday", update_id=2, message_id=50)
    assert await draft_flow.get_state() == DraftStates.confirming.state

    await draft_flow.feed_callback("sconf:yes", update_id=3, message_id=51)

    assert await draft_flow.get_state() is None
    final_call = draft_flow.last_call()
    assert isinstance(final_call, SendMessage)
    assert _short_id(draft_id) in final_call.text

    pending_posts = await draft_flow.store.list_pending_posts(USER_ID, limit=10)
    assert len(pending_posts) == 1
    post = pending_posts[0]
    assert post.chat_id == DESTINATION_CHAT_ID
    assert post.kind == "media"
    assert post.caption == "Командный draft publish"
    assert await draft_flow.store.get_post_media(post.id) == [{"type": "photo", "file_id": "photo-publish-1"}]
    assert await draft_flow.store.get_draft(draft_id) is not None


@pytest.mark.asyncio
async def test_draft_publish_rechecks_access_on_confirm(draft_flow: DraftFlowHarness) -> None:
    draft_id = await draft_flow.store.create_draft(
        author_user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        kind="text",
        text="Исчезающий черновик",
    )

    await draft_flow.feed_message(f"/draft_post {draft_id[:8]}", update_id=1, message_id=10)
    await draft_flow.feed_callback("tp:quick:next_monday", update_id=2, message_id=50)

    assert await draft_flow.get_state() == DraftStates.confirming.state
    assert await draft_flow.store.delete_draft(draft_id, USER_ID) is True

    await draft_flow.feed_callback("sconf:yes", update_id=3, message_id=51)

    assert await draft_flow.get_state() is None
    final_call = draft_flow.last_call()
    assert isinstance(final_call, SendMessage)
    assert final_call.text == tr("ru", "draft_missing")
    assert await draft_flow.store.list_pending_posts(USER_ID, limit=10) == []


@pytest.mark.asyncio
async def test_draft_post_command_rejects_viewer_role(draft_flow: DraftFlowHarness) -> None:
    draft_id = await draft_flow.store.create_draft(
        author_user_id=ALT_USER_ID,
        team_id=draft_flow.viewer_team_id,
        chat_id=DESTINATION_CHAT_ID,
        kind="text",
        text="Viewer cannot publish this",
    )

    await draft_flow.feed_message(f"/draft_post {draft_id[:8]}", update_id=1, message_id=10)

    assert await draft_flow.get_state() is None
    call = draft_flow.last_call()
    assert isinstance(call, SendMessage)
    assert call.text == tr("ru", "draft_missing")
    assert await draft_flow.store.list_pending_posts(USER_ID, limit=10) == []


@pytest.mark.asyncio
async def test_drafts_filter_switches_to_team_scope_without_leaking_personal_drafts(draft_flow: DraftFlowHarness) -> None:
    personal_id = await draft_flow.store.create_draft(
        author_user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        kind="text",
        text="Личный черновик",
    )
    team_id = await draft_flow.store.create_draft(
        author_user_id=ALT_USER_ID,
        team_id=draft_flow.viewer_team_id,
        chat_id=DESTINATION_CHAT_ID,
        kind="text",
        text="Командный черновик",
    )
    hidden_id = await draft_flow.store.create_draft(
        author_user_id=ALT_USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        kind="text",
        text="Чужой личный черновик",
    )

    await draft_flow.feed_message("/drafts", update_id=1, message_id=10)
    first_call = draft_flow.last_call()
    assert isinstance(first_call, SendMessage)
    assert _short_id(personal_id) in first_call.text
    assert _short_id(team_id) in first_call.text
    assert _short_id(hidden_id) not in first_call.text

    await draft_flow.feed_callback("dscope:team", update_id=2, message_id=50)

    call = draft_flow.last_call()
    assert isinstance(call, EditMessageText)
    assert call.text.startswith(f"Черновики: {tr('ru', 'draft_filter_team')}")
    assert _short_id(team_id) in call.text
    assert _short_id(personal_id) not in call.text
    assert _short_id(hidden_id) not in call.text


@pytest.mark.asyncio
async def test_drafts_pagination_uses_page_callbacks(draft_flow: DraftFlowHarness) -> None:
    for index in range(6):
        await draft_flow.store.create_draft(
            author_user_id=USER_ID,
            chat_id=DESTINATION_CHAT_ID,
            kind="text",
            text=f"draft {index}",
        )

    await draft_flow.feed_message("/drafts", update_id=1, message_id=10)

    first_page = draft_flow.last_call()
    assert isinstance(first_page, SendMessage)
    first_callbacks = _callback_data(first_page)
    assert len([item for item in first_callbacks if item.startswith("dopen:all:0:")]) == 5
    assert "dpage:all:1" in first_callbacks

    await draft_flow.feed_callback("dpage:all:1", update_id=2, message_id=50)

    second_page = draft_flow.last_call()
    assert isinstance(second_page, EditMessageText)
    second_callbacks = _callback_data(second_page)
    assert len([item for item in second_callbacks if item.startswith("dopen:all:1:")]) == 1
    assert "dpage:all:0" in second_callbacks
    assert "dpage:all:2" not in second_callbacks


@pytest.mark.asyncio
async def test_draft_detail_shows_manager_actions_for_personal_draft(draft_flow: DraftFlowHarness) -> None:
    draft_id = await draft_flow.store.create_draft(
        author_user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        kind="text",
        text="Личный черновик для действий",
    )

    await draft_flow.feed_message("/drafts", update_id=1, message_id=10)
    await draft_flow.feed_callback(f"dopen:all:0:{draft_id}", update_id=2, message_id=50)

    call = draft_flow.last_call()
    assert isinstance(call, EditMessageText)
    callbacks = _callback_data(call)
    assert f"dact:edit:{draft_id}" in callbacks
    assert f"ddelask:all:0:{draft_id}" in callbacks
    assert f"dact:publish:{draft_id}" in callbacks
    assert f"dback:all:0" in callbacks


@pytest.mark.asyncio
async def test_draft_detail_hides_manager_actions_for_viewer_role(draft_flow: DraftFlowHarness) -> None:
    draft_id = await draft_flow.store.create_draft(
        author_user_id=ALT_USER_ID,
        team_id=draft_flow.viewer_team_id,
        chat_id=DESTINATION_CHAT_ID,
        kind="text",
        text="Командный просмотр",
    )

    await draft_flow.feed_message("/drafts", update_id=1, message_id=10)
    await draft_flow.feed_callback("dscope:team", update_id=2, message_id=50)
    await draft_flow.feed_callback(f"dopen:team:0:{draft_id}", update_id=3, message_id=50)

    call = draft_flow.last_call()
    assert isinstance(call, EditMessageText)
    callbacks = _callback_data(call)
    assert callbacks == ["dback:team:0"]
    assert tr("ru", "draft_actions_view_only") in call.text


@pytest.mark.asyncio
async def test_draft_open_does_not_leak_unavailable_draft(draft_flow: DraftFlowHarness) -> None:
    visible_id = await draft_flow.store.create_draft(
        author_user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        kind="text",
        text="Видимый черновик",
    )
    hidden_id = await draft_flow.store.create_draft(
        author_user_id=ALT_USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        kind="text",
        text="Скрытый черновик",
    )

    await draft_flow.feed_message("/drafts", update_id=1, message_id=10)
    initial_list = draft_flow.last_call()
    assert isinstance(initial_list, SendMessage)
    before_calls = len(draft_flow.bot.calls)
    await draft_flow.feed_callback(f"dopen:all:0:{hidden_id}", update_id=2, message_id=50)

    recent_calls = draft_flow.bot.calls[before_calls:]
    assert len(recent_calls) == 1
    assert isinstance(recent_calls[0], AnswerCallbackQuery)
    assert recent_calls[0].text == tr("ru", "draft_missing")
    assert _short_id(visible_id) in initial_list.text
    assert _short_id(hidden_id) not in initial_list.text
