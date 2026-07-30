from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from core.state import StateStore
from telegram.i18n import tr
from telegram.handlers import keyboards as kb, helpers as h


async def _handle_team_invite_start(
    store: StateStore, message: Message, state: FSMContext, *, user_id: int, token: str
) -> None:
    lang = await h._user_lang(store, user_id)
    await state.clear()
    result = await store.accept_team_invite(token, user_id)
    team = result.team
    role = result.role

    if result.status == "accepted" and team is not None and role is not None:
        await message.answer(
            tr(
                lang,
                "team_invite_accept_ok",
                team_id=kb._short_id(team.id),
                team_name=team.name,
                role=kb._team_role_label(lang, role),
            ),
            reply_markup=await h._main_menu_for(store, user_id),
        )
        return

    if result.status == "already_member" and team is not None and role is not None:
        await message.answer(
            tr(
                lang,
                "team_invite_already_member",
                team_id=kb._short_id(team.id),
                team_name=team.name,
                role=kb._team_role_label(lang, role),
            ),
            reply_markup=await h._main_menu_for(store, user_id),
        )
        return

    key = {
        "expired": "team_invite_expired",
        "used": "team_invite_used",
    }.get(result.status, "team_invite_missing")
    await message.answer(tr(lang, key), reply_markup=await h._main_menu_for(store, user_id))


def build_router(store: StateStore) -> Router:
    router = Router(name="teams")

    @router.message(Command("team_create"))
    async def cmd_team_create(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await h._user_lang(store, message.from_user.id)
        await state.clear()
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await message.answer(tr(lang, "team_create_usage"), reply_markup=await h._main_menu_for(store, message.from_user.id))
            return

        team_name = parts[1].strip()
        team_id = await store.create_team(message.from_user.id, team_name)
        await message.answer(
            tr(
                lang,
                "team_create_ok",
                team_id=kb._short_id(team_id),
                team_name=team_name,
                role=kb._team_role_label(lang, "owner"),
            ),
            reply_markup=await h._main_menu_for(store, message.from_user.id),
        )

    @router.message(Command("team_invite"))
    async def cmd_team_invite(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await h._user_lang(store, message.from_user.id)
        await state.clear()
        parts = (message.text or "").split(maxsplit=2)
        if len(parts) < 2 or not parts[1].strip():
            await message.answer(tr(lang, "team_invite_usage"), reply_markup=await h._main_menu_for(store, message.from_user.id))
            return

        team_ref = parts[1].strip().lower()
        role = parts[2].strip().lower() if len(parts) == 3 and parts[2].strip() else "viewer"
        if role not in {"viewer", "editor"}:
            await message.answer(tr(lang, "team_invite_role_invalid"), reply_markup=await h._main_menu_for(store, message.from_user.id))
            return

        owned_teams = await store.list_owned_teams(message.from_user.id, limit=200)
        team_id = h._resolve_team_id(owned_teams, team_ref)
        if team_id is None:
            await message.answer(tr(lang, "team_missing"), reply_markup=await h._main_menu_for(store, message.from_user.id))
            return

        try:
            invite = await store.create_team_invite(team_id, message.from_user.id, role)
        except ValueError:
            await message.answer(tr(lang, "team_missing"), reply_markup=await h._main_menu_for(store, message.from_user.id))
            return

        team = next((item for item in owned_teams if item.id == team_id), None)
        if team is None:
            await message.answer(tr(lang, "team_missing"), reply_markup=await h._main_menu_for(store, message.from_user.id))
            return

        bot_user = await message.bot.me()
        start_payload = f"ti_{invite.token}"
        bot_username = getattr(bot_user, "username", None)
        invite_link = f"https://t.me/{bot_username}?start={start_payload}" if bot_username else f"/start {start_payload}"
        tz_name = await store.get_user_timezone(message.from_user.id) or "UTC"
        await message.answer(
            tr(
                lang,
                "team_invite_created",
                team_id=kb._short_id(team.id),
                team_name=team.name,
                role=kb._team_role_label(lang, role),
                expires_at=kb._format_local(invite.expires_at, tz_name),
                tz_name=tz_name,
                link=invite_link,
            ),
            reply_markup=await h._main_menu_for(store, message.from_user.id),
        )

    @router.message(Command("team_members"))
    async def cmd_team_members(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await h._user_lang(store, message.from_user.id)
        await state.clear()
        teams = await store.list_user_teams(message.from_user.id, limit=200)
        if not teams:
            await message.answer(tr(lang, "team_members_none"), reply_markup=await h._main_menu_for(store, message.from_user.id))
            return

        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 2 and parts[1].strip():
            team_id = h._resolve_team_id(teams, parts[1].strip().lower())
            if team_id is None:
                await message.answer(tr(lang, "team_missing"), reply_markup=await h._main_menu_for(store, message.from_user.id))
                return
        elif len(teams) == 1:
            team_id = teams[0].id
        else:
            lines: list[str] = []
            for team in teams:
                role = await store.get_team_member_role(team.id, message.from_user.id)
                if role is None:
                    continue
                lines.append(
                    tr(
                        lang,
                        "team_members_choose_item",
                        team_id=kb._short_id(team.id),
                        team_name=team.name,
                        role=kb._team_role_label(lang, role),
                    )
                )
            await message.answer(
                tr(lang, "team_members_choose", lines="\n".join(lines)),
                reply_markup=await h._main_menu_for(store, message.from_user.id),
            )
            return

        team = next((item for item in teams if item.id == team_id), None)
        role = await store.get_team_member_role(team_id, message.from_user.id)
        if team is None or role is None:
            await message.answer(tr(lang, "team_missing"), reply_markup=await h._main_menu_for(store, message.from_user.id))
            return

        members = await store.list_team_members(team_id)
        lines = "\n".join(
            tr(
                lang,
                "team_members_item",
                role=kb._team_role_label(lang, member.role),
                user_id=member.user_id,
            )
            for member in members
        )
        await message.answer(
            tr(
                lang,
                "team_members_header",
                team_id=kb._short_id(team.id),
                team_name=team.name,
                role=kb._team_role_label(lang, role),
                lines=lines,
            ),
            reply_markup=await h._main_menu_for(store, message.from_user.id),
        )

    return router
