from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterable

import aiosqlite

from core.rbac import DraftPermissions, can_create_team_draft, resolve_draft_permissions


@dataclass(frozen=True)
class Destination:
    chat_id: int
    type: str
    title: str
    username: str | None
    bot_status: str
    bot_can_post: bool | None


@dataclass(frozen=True)
class Team:
    id: str
    owner_user_id: int
    name: str
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class TeamMember:
    team_id: str
    user_id: int
    role: str
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class TeamInvite:
    token: str
    team_id: str
    role: str
    created_by_user_id: int
    created_at: int
    expires_at: int
    accepted_by_user_id: int | None
    accepted_at: int | None


@dataclass(frozen=True)
class TeamInviteAcceptance:
    status: str
    team: Team | None
    role: str | None


@dataclass(frozen=True)
class DraftRow:
    id: str
    team_id: str | None
    author_user_id: int
    chat_id: int
    kind: str
    text: str | None
    entities_json: str | None
    caption: str | None
    caption_entities_json: str | None
    caption_above: int | None
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class ScheduledPostRow:
    id: str
    user_id: int
    chat_id: int
    scheduled_at_utc: int
    status: str
    kind: str
    text: str | None
    entities_json: str | None
    caption: str | None
    caption_entities_json: str | None
    caption_above: int | None
    attempts: int
    next_retry_at_utc: int | None
    created_at: int
    sent_at: int | None
    last_error: str | None


@dataclass(frozen=True)
class RecurringPattern:
    id: str
    user_id: int
    chat_id: int
    interval_type: str
    weekdays_mask: int | None
    time_of_day_minutes: int
    timezone: str
    start_at_utc: int
    end_at_utc: int | None
    max_occurrences: int | None
    current_count: int
    is_active: bool
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class RecurringInstance:
    pattern_id: str
    post_id: str
    ordinal: int
    scheduled_for_utc: int
    created_at: int


@dataclass(frozen=True)
class RecurringPatternSummary:
    pattern: RecurringPattern
    destination_title: str
    destination_username: str | None
    next_post_id: str | None
    next_scheduled_at_utc: int | None
    next_post_status: str | None


class StateStore:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def _execute_fetchone(self, query: str, params: tuple[object, ...] = ()) -> aiosqlite.Row | None:
        cur = await self._conn.execute(query, params)
        try:
            return await cur.fetchone()
        finally:
            await cur.close()

    async def migrate(self) -> None:
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                timezone TEXT NULL,
                language TEXT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS destinations (
                chat_id INTEGER PRIMARY KEY,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                username TEXT NULL,
                bot_status TEXT NOT NULL,
                bot_can_post INTEGER NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_destinations (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                linked_via TEXT NOT NULL,
                linked_at INTEGER NOT NULL,
                PRIMARY KEY (user_id, chat_id),
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                FOREIGN KEY (chat_id) REFERENCES destinations(chat_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS teams (
                id TEXT PRIMARY KEY,
                owner_user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY (owner_user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_teams_owner_created
                ON teams(owner_user_id, created_at);

            CREATE TABLE IF NOT EXISTS team_members (
                team_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (team_id, user_id),
                CHECK (role IN ('owner', 'editor', 'viewer')),
                FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_team_members_user_team
                ON team_members(user_id, team_id);

            CREATE UNIQUE INDEX IF NOT EXISTS idx_team_members_single_owner
                ON team_members(team_id)
                WHERE role='owner';

            CREATE TABLE IF NOT EXISTS team_invites (
                token TEXT PRIMARY KEY,
                team_id TEXT NOT NULL,
                role TEXT NOT NULL,
                created_by_user_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                accepted_by_user_id INTEGER NULL,
                accepted_at INTEGER NULL,
                CHECK (role IN ('editor', 'viewer')),
                CHECK (expires_at > created_at),
                FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
                FOREIGN KEY (created_by_user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                FOREIGN KEY (accepted_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_team_invites_team_created
                ON team_invites(team_id, created_at);

            CREATE INDEX IF NOT EXISTS idx_team_invites_expires
                ON team_invites(expires_at, accepted_at);

            CREATE TABLE IF NOT EXISTS drafts (
                id TEXT PRIMARY KEY,
                team_id TEXT NULL,
                author_user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                text TEXT NULL,
                entities_json TEXT NULL,
                caption TEXT NULL,
                caption_entities_json TEXT NULL,
                caption_above INTEGER NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                CHECK (kind IN ('text', 'media')),
                FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
                FOREIGN KEY (author_user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                FOREIGN KEY (chat_id) REFERENCES destinations(chat_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_drafts_author_created
                ON drafts(author_user_id, created_at);

            CREATE INDEX IF NOT EXISTS idx_drafts_team_created
                ON drafts(team_id, created_at);

            CREATE TABLE IF NOT EXISTS draft_media (
                draft_id TEXT NOT NULL,
                idx INTEGER NOT NULL,
                type TEXT NOT NULL,
                file_id TEXT NOT NULL,
                PRIMARY KEY (draft_id, idx),
                FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                scheduled_at_utc INTEGER NOT NULL,
                status TEXT NOT NULL,
                kind TEXT NOT NULL,
                text TEXT NULL,
                entities_json TEXT NULL,
                caption TEXT NULL,
                caption_entities_json TEXT NULL,
                caption_above INTEGER NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_retry_at_utc INTEGER NULL,
                created_at INTEGER NOT NULL,
                sent_at INTEGER NULL,
                last_error TEXT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                FOREIGN KEY (chat_id) REFERENCES destinations(chat_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_scheduled_due
                ON scheduled_posts(status, scheduled_at_utc, next_retry_at_utc);

            CREATE TABLE IF NOT EXISTS scheduled_post_media (
                post_id TEXT NOT NULL,
                idx INTEGER NOT NULL,
                type TEXT NOT NULL,
                file_id TEXT NOT NULL,
                PRIMARY KEY (post_id, idx),
                FOREIGN KEY (post_id) REFERENCES scheduled_posts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS recurring_patterns (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                interval_type TEXT NOT NULL,
                weekdays_mask INTEGER NULL,
                time_of_day_minutes INTEGER NOT NULL,
                timezone TEXT NOT NULL,
                start_at_utc INTEGER NOT NULL,
                end_at_utc INTEGER NULL,
                max_occurrences INTEGER NULL,
                current_count INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                CHECK (time_of_day_minutes BETWEEN 0 AND 1439),
                CHECK (weekdays_mask IS NULL OR weekdays_mask BETWEEN 1 AND 127),
                CHECK (max_occurrences IS NULL OR max_occurrences > 0),
                CHECK (current_count >= 0),
                CHECK (is_active IN (0, 1)),
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                FOREIGN KEY (chat_id) REFERENCES destinations(chat_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_recurring_patterns_user_active
                ON recurring_patterns(user_id, is_active, created_at);

            CREATE INDEX IF NOT EXISTS idx_recurring_patterns_chat_active
                ON recurring_patterns(chat_id, is_active);

            CREATE TABLE IF NOT EXISTS recurring_instances (
                pattern_id TEXT NOT NULL,
                post_id TEXT NOT NULL UNIQUE,
                ordinal INTEGER NOT NULL,
                scheduled_for_utc INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (pattern_id, ordinal),
                CHECK (ordinal > 0),
                FOREIGN KEY (pattern_id) REFERENCES recurring_patterns(id) ON DELETE CASCADE,
                FOREIGN KEY (post_id) REFERENCES scheduled_posts(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_recurring_instances_pattern_scheduled
                ON recurring_instances(pattern_id, scheduled_for_utc);
            """
        )

        user_columns = await self._conn.execute_fetchall("PRAGMA table_info(users)")
        user_column_names = {str(row["name"]) for row in user_columns}
        if "language" not in user_column_names:
            await self._conn.execute("ALTER TABLE users ADD COLUMN language TEXT NULL")

        now = int(time.time())
        await self._conn.execute(
            """
            UPDATE team_members
            SET role='owner', updated_at=?
            WHERE (team_id, user_id) IN (
                SELECT id, owner_user_id
                FROM teams
            )
              AND role <> 'owner'
            """,
            (now,),
        )
        await self._conn.execute(
            """
            INSERT INTO team_members(team_id, user_id, role, created_at, updated_at)
            SELECT t.id, t.owner_user_id, 'owner', ?, ?
            FROM teams t
            WHERE NOT EXISTS (
                SELECT 1
                FROM team_members tm
                WHERE tm.team_id = t.id
                  AND tm.user_id = t.owner_user_id
            )
            """,
            (now, now),
        )
        await self._conn.commit()

    async def ensure_user(self, user_id: int) -> None:
        now = int(time.time())
        await self._conn.execute(
            """
            INSERT INTO users(user_id, timezone, language, created_at, updated_at)
            VALUES(?, NULL, NULL, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET updated_at=excluded.updated_at
            """,
            (user_id, now, now),
        )
        await self._conn.commit()

    async def get_user_timezone(self, user_id: int) -> str | None:
        row = await self._execute_fetchone(
            "SELECT timezone FROM users WHERE user_id=?",
            (user_id,),
        )
        return None if row is None else row["timezone"]

    async def set_user_timezone(self, user_id: int, tz_name: str) -> None:
        now = int(time.time())
        await self._conn.execute(
            "UPDATE users SET timezone=?, updated_at=? WHERE user_id=?",
            (tz_name, now, user_id),
        )
        await self._conn.commit()

    async def get_user_language(self, user_id: int) -> str | None:
        row = await self._execute_fetchone(
            "SELECT language FROM users WHERE user_id=?",
            (user_id,),
        )
        return None if row is None else row["language"]

    async def set_user_language(self, user_id: int, language: str) -> None:
        now = int(time.time())
        await self._conn.execute(
            "UPDATE users SET language=?, updated_at=? WHERE user_id=?",
            (language, now, user_id),
        )
        await self._conn.commit()

    async def upsert_destination(
        self,
        chat_id: int,
        type_: str,
        title: str,
        username: str | None,
        bot_status: str,
        bot_can_post: bool | None,
    ) -> None:
        now = int(time.time())
        await self._conn.execute(
            """
            INSERT INTO destinations(chat_id, type, title, username, bot_status, bot_can_post, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                type=excluded.type,
                title=excluded.title,
                username=excluded.username,
                bot_status=excluded.bot_status,
                bot_can_post=excluded.bot_can_post,
                updated_at=excluded.updated_at
            """,
            (chat_id, type_, title, username, bot_status, int(bot_can_post) if bot_can_post is not None else None, now),
        )
        await self._conn.commit()

    async def link_user_destination(self, user_id: int, chat_id: int, linked_via: str) -> None:
        now = int(time.time())
        await self._conn.execute(
            """
            INSERT INTO user_destinations(user_id, chat_id, linked_via, linked_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET
                linked_via=excluded.linked_via,
                linked_at=excluded.linked_at
            """,
            (user_id, chat_id, linked_via, now),
        )
        await self._conn.commit()

    async def count_user_destinations(self, user_id: int) -> int:
        row = await self._execute_fetchone(
            "SELECT COUNT(1) AS cnt FROM user_destinations WHERE user_id=?",
            (user_id,),
        )
        return 0 if row is None else int(row["cnt"])

    async def list_user_destinations(self, user_id: int, offset: int, limit: int) -> list[Destination]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT d.chat_id, d.type, d.title, d.username, d.bot_status, d.bot_can_post
            FROM user_destinations ud
            JOIN destinations d ON d.chat_id = ud.chat_id
            WHERE ud.user_id=?
            ORDER BY d.updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        )
        out: list[Destination] = []
        for r in rows:
            out.append(
                Destination(
                    chat_id=int(r["chat_id"]),
                    type=str(r["type"]),
                    title=str(r["title"]),
                    username=r["username"],
                    bot_status=str(r["bot_status"]),
                    bot_can_post=None if r["bot_can_post"] is None else bool(int(r["bot_can_post"])),
                )
            )
        return out

    async def create_team(self, owner_user_id: int, name: str) -> str:
        now = int(time.time())
        team_id = uuid.uuid4().hex
        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            await self._conn.execute(
                """
                INSERT INTO teams(id, owner_user_id, name, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (team_id, owner_user_id, name, now, now),
            )
            await self._conn.execute(
                """
                INSERT INTO team_members(team_id, user_id, role, created_at, updated_at)
                VALUES(?, ?, 'owner', ?, ?)
                """,
                (team_id, owner_user_id, now, now),
            )
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise
        return team_id

    async def get_team(self, team_id: str) -> Team | None:
        row = await self._execute_fetchone(
            "SELECT * FROM teams WHERE id=?",
            (team_id,),
        )
        return None if row is None else self._row_to_team(row)

    async def list_owned_teams(self, owner_user_id: int, offset: int = 0, limit: int = 20) -> list[Team]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT *
            FROM teams
            WHERE owner_user_id=?
            ORDER BY created_at DESC, rowid DESC, id ASC
            LIMIT ? OFFSET ?
            """,
            (owner_user_id, limit, offset),
        )
        return [self._row_to_team(row) for row in rows]

    async def list_writable_teams(self, user_id: int, offset: int = 0, limit: int = 20) -> list[Team]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT t.*
            FROM team_members tm
            JOIN teams t ON t.id = tm.team_id
            WHERE tm.user_id=?
              AND tm.role IN ('owner', 'editor')
            ORDER BY
                CASE tm.role
                    WHEN 'owner' THEN 0
                    ELSE 1
                END,
                t.created_at DESC,
                t.id ASC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        )
        return [self._row_to_team(row) for row in rows]

    async def list_user_teams(self, user_id: int, offset: int = 0, limit: int = 20) -> list[Team]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT t.*
            FROM team_members tm
            JOIN teams t ON t.id = tm.team_id
            WHERE tm.user_id=?
            ORDER BY
                CASE tm.role
                    WHEN 'owner' THEN 0
                    WHEN 'editor' THEN 1
                    ELSE 2
                END,
                t.created_at DESC,
                t.id ASC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        )
        return [self._row_to_team(row) for row in rows]

    async def upsert_team_member(self, team_id: str, user_id: int, role: str) -> TeamMember:
        team_row = await self._execute_fetchone(
            "SELECT owner_user_id FROM teams WHERE id=?",
            (team_id,),
        )
        if team_row is not None:
            owner_user_id = int(team_row["owner_user_id"])
            if user_id == owner_user_id and role != "owner":
                raise ValueError("Team owner role cannot be changed via upsert_team_member")
            if user_id != owner_user_id and role == "owner":
                raise ValueError("Team owner transfer requires a dedicated flow")

        now = int(time.time())
        await self._conn.execute(
            """
            INSERT INTO team_members(team_id, user_id, role, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(team_id, user_id) DO UPDATE SET
                role=excluded.role,
                updated_at=excluded.updated_at
            """,
            (team_id, user_id, role, now, now),
        )
        await self._conn.commit()

        row = await self._execute_fetchone(
            "SELECT * FROM team_members WHERE team_id=? AND user_id=?",
            (team_id, user_id),
        )
        if row is None:
            raise ValueError(f"Team member {team_id}:{user_id} was not persisted")
        return self._row_to_team_member(row)

    async def get_team_member_role(self, team_id: str, user_id: int) -> str | None:
        row = await self._execute_fetchone(
            "SELECT role FROM team_members WHERE team_id=? AND user_id=?",
            (team_id, user_id),
        )
        return None if row is None else str(row["role"])

    async def list_team_members(self, team_id: str) -> list[TeamMember]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT *
            FROM team_members
            WHERE team_id=?
            ORDER BY
                CASE role
                    WHEN 'owner' THEN 0
                    WHEN 'editor' THEN 1
                    ELSE 2
                END,
                created_at ASC,
                user_id ASC
            """,
            (team_id,),
        )
        return [self._row_to_team_member(row) for row in rows]

    async def create_team_invite(
        self,
        team_id: str,
        created_by_user_id: int,
        role: str,
        *,
        ttl_seconds: int = 7 * 24 * 60 * 60,
    ) -> TeamInvite:
        if role not in {"editor", "viewer"}:
            raise ValueError(f"Unsupported team invite role: {role}")
        if ttl_seconds <= 0:
            raise ValueError("Team invite ttl_seconds must be positive")

        team_row = await self._execute_fetchone(
            "SELECT owner_user_id FROM teams WHERE id=?",
            (team_id,),
        )
        if team_row is None or int(team_row["owner_user_id"]) != created_by_user_id:
            raise ValueError("Only team owner can create invites")

        now = int(time.time())
        token = uuid.uuid4().hex
        expires_at = now + ttl_seconds
        await self._conn.execute(
            """
            INSERT INTO team_invites(token, team_id, role, created_by_user_id, created_at, expires_at, accepted_by_user_id, accepted_at)
            VALUES(?, ?, ?, ?, ?, ?, NULL, NULL)
            """,
            (token, team_id, role, created_by_user_id, now, expires_at),
        )
        await self._conn.commit()

        row = await self._execute_fetchone(
            "SELECT * FROM team_invites WHERE token=?",
            (token,),
        )
        if row is None:
            raise ValueError(f"Team invite {token} was not persisted")
        return self._row_to_team_invite(row)

    async def get_team_invite(self, token: str) -> TeamInvite | None:
        row = await self._execute_fetchone(
            "SELECT * FROM team_invites WHERE token=?",
            (token,),
        )
        return None if row is None else self._row_to_team_invite(row)

    async def accept_team_invite(self, token: str, user_id: int) -> TeamInviteAcceptance:
        now = int(time.time())
        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            invite_row = await self._execute_fetchone(
                "SELECT * FROM team_invites WHERE token=?",
                (token,),
            )
            if invite_row is None:
                await self._conn.rollback()
                return TeamInviteAcceptance(status="missing", team=None, role=None)

            invite = self._row_to_team_invite(invite_row)
            team_row = await self._execute_fetchone(
                "SELECT * FROM teams WHERE id=?",
                (invite.team_id,),
            )
            team = None if team_row is None else self._row_to_team(team_row)
            if team is None:
                await self._conn.rollback()
                return TeamInviteAcceptance(status="missing", team=None, role=None)
            if invite.accepted_at is not None:
                await self._conn.rollback()
                return TeamInviteAcceptance(status="used", team=team, role=invite.role)
            if invite.expires_at <= now:
                await self._conn.rollback()
                return TeamInviteAcceptance(status="expired", team=team, role=invite.role)

            existing_member = await self._execute_fetchone(
                "SELECT role FROM team_members WHERE team_id=? AND user_id=?",
                (invite.team_id, user_id),
            )
            if existing_member is not None:
                await self._conn.rollback()
                return TeamInviteAcceptance(status="already_member", team=team, role=str(existing_member["role"]))

            await self._conn.execute(
                """
                INSERT INTO team_members(team_id, user_id, role, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (invite.team_id, user_id, invite.role, now, now),
            )
            await self._conn.execute(
                """
                UPDATE team_invites
                SET accepted_by_user_id=?, accepted_at=?
                WHERE token=?
                """,
                (user_id, now, token),
            )
            await self._conn.commit()
            return TeamInviteAcceptance(status="accepted", team=team, role=invite.role)
        except Exception:
            await self._conn.rollback()
            raise

    @staticmethod
    def _normalize_draft_payload(
        *,
        kind: str,
        text: str | None,
        entities_json: str | None,
        caption: str | None,
        caption_entities_json: str | None,
        caption_above: bool | None,
        media_items: list[dict[str, str]] | None,
    ) -> tuple[str | None, str | None, str | None, str | None, int | None, list[dict[str, str]]]:
        if kind not in {"text", "media"}:
            raise ValueError(f"Unsupported draft kind: {kind}")

        items = list(media_items or [])
        if kind == "text":
            if not str(text or "").strip():
                raise ValueError("Text draft must contain text")
            if items:
                raise ValueError("Text draft cannot contain media items")
            if caption is not None or caption_entities_json is not None or caption_above is not None:
                raise ValueError("Text draft cannot contain media caption fields")
            return text, entities_json, None, None, None, []

        if items == []:
            raise ValueError("Media draft must contain at least one media item")
        if text is not None or entities_json is not None:
            raise ValueError("Media draft must use caption fields instead of text fields")
        return None, None, caption, caption_entities_json, None if caption_above is None else int(caption_above), items

    async def _get_draft_access(self, draft_id: str, user_id: int) -> tuple[DraftRow | None, str | None]:
        draft = await self.get_draft(draft_id)
        if draft is None or draft.team_id is None:
            return draft, None
        return draft, await self.get_team_member_role(draft.team_id, user_id)

    async def create_draft(
        self,
        *,
        author_user_id: int,
        chat_id: int,
        kind: str,
        team_id: str | None = None,
        text: str | None = None,
        entities_json: str | None = None,
        caption: str | None = None,
        caption_entities_json: str | None = None,
        caption_above: bool | None = None,
        media_items: list[dict[str, str]] | None = None,
    ) -> str:
        if team_id is not None:
            team_role = await self.get_team_member_role(team_id, author_user_id)
            if not can_create_team_draft(team_role):
                raise ValueError("User must be an owner or editor to write team drafts")

        normalized_text, normalized_entities, normalized_caption, normalized_caption_entities, normalized_caption_above, items = (
            self._normalize_draft_payload(
                kind=kind,
                text=text,
                entities_json=entities_json,
                caption=caption,
                caption_entities_json=caption_entities_json,
                caption_above=caption_above,
                media_items=media_items,
            )
        )

        now = int(time.time())
        draft_id = uuid.uuid4().hex

        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            await self._conn.execute(
                """
                INSERT INTO drafts(
                    id,
                    team_id,
                    author_user_id,
                    chat_id,
                    kind,
                    text,
                    entities_json,
                    caption,
                    caption_entities_json,
                    caption_above,
                    created_at,
                    updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    team_id,
                    author_user_id,
                    chat_id,
                    kind,
                    normalized_text,
                    normalized_entities,
                    normalized_caption,
                    normalized_caption_entities,
                    normalized_caption_above,
                    now,
                    now,
                ),
            )

            for idx, item in enumerate(items):
                await self._conn.execute(
                    "INSERT INTO draft_media(draft_id, idx, type, file_id) VALUES(?, ?, ?, ?)",
                    (draft_id, idx, item["type"], item["file_id"]),
                )

            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

        return draft_id

    async def get_draft(self, draft_id: str) -> DraftRow | None:
        row = await self._execute_fetchone(
            "SELECT * FROM drafts WHERE id=?",
            (draft_id,),
        )
        return None if row is None else self._row_to_draft(row)

    async def get_draft_media(self, draft_id: str) -> list[dict[str, str]]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT idx, type, file_id
            FROM draft_media
            WHERE draft_id=?
            ORDER BY idx ASC
            """,
            (draft_id,),
        )
        return [{"type": str(row["type"]), "file_id": str(row["file_id"])} for row in rows]

    async def get_draft_permissions(self, draft_id: str, user_id: int) -> DraftPermissions | None:
        draft, team_role = await self._get_draft_access(draft_id, user_id)
        if draft is None:
            return None
        return resolve_draft_permissions(
            draft_author_user_id=draft.author_user_id,
            acting_user_id=user_id,
            team_id=draft.team_id,
            team_role=team_role,
        )

    async def list_drafts(
        self,
        user_id: int,
        *,
        scope: str = "all",
        team_id: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[DraftRow]:
        if scope not in {"all", "mine", "team"}:
            raise ValueError(f"Unsupported draft scope: {scope}")

        query = """
            SELECT d.*
            FROM drafts d
            WHERE
        """
        params: list[object] = []

        if team_id is not None:
            query += " d.team_id=?"
            params.append(team_id)
            if scope == "mine":
                query += """
                    AND d.author_user_id=?
                    AND EXISTS (
                        SELECT 1
                        FROM team_members tm
                        WHERE tm.team_id = d.team_id
                          AND tm.user_id = ?
                    )
                """
                params.extend((user_id, user_id))
            else:
                query += """
                    AND EXISTS (
                        SELECT 1
                        FROM team_members tm
                        WHERE tm.team_id = d.team_id
                          AND tm.user_id = ?
                    )
                """
                params.append(user_id)
        elif scope == "all":
            query += """
                (
                    (d.team_id IS NULL AND d.author_user_id=?)
                    OR EXISTS (
                        SELECT 1
                        FROM team_members tm
                        WHERE tm.team_id = d.team_id
                          AND tm.user_id = ?
                    )
                )
            """
            params.extend((user_id, user_id))
        elif scope == "mine":
            query += """
                (
                    (d.team_id IS NULL AND d.author_user_id=?)
                    OR (
                        d.author_user_id=?
                        AND EXISTS (
                            SELECT 1
                            FROM team_members tm
                            WHERE tm.team_id = d.team_id
                              AND tm.user_id = ?
                        )
                    )
                )
            """
            params.extend((user_id, user_id, user_id))
        else:
            query += """
                d.team_id IS NOT NULL
                AND EXISTS (
                    SELECT 1
                    FROM team_members tm
                    WHERE tm.team_id = d.team_id
                      AND tm.user_id = ?
                )
            """
            params.append(user_id)

        query += " ORDER BY d.updated_at DESC, d.created_at DESC, d.id ASC LIMIT ? OFFSET ?"
        params.extend((limit, offset))
        rows = await self._conn.execute_fetchall(query, tuple(params))
        return [self._row_to_draft(row) for row in rows]

    async def update_draft(
        self,
        draft_id: str,
        user_id: int,
        *,
        chat_id: int,
        kind: str,
        text: str | None = None,
        entities_json: str | None = None,
        caption: str | None = None,
        caption_entities_json: str | None = None,
        caption_above: bool | None = None,
        media_items: list[dict[str, str]] | None = None,
    ) -> bool:
        permissions = await self.get_draft_permissions(draft_id, user_id)
        if permissions is None or not permissions.can_edit:
            return False

        normalized_text, normalized_entities, normalized_caption, normalized_caption_entities, normalized_caption_above, items = (
            self._normalize_draft_payload(
                kind=kind,
                text=text,
                entities_json=entities_json,
                caption=caption,
                caption_entities_json=caption_entities_json,
                caption_above=caption_above,
                media_items=media_items,
            )
        )
        now = int(time.time())

        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            cur = await self._conn.execute(
                """
                UPDATE drafts
                SET chat_id=?,
                    kind=?,
                    text=?,
                    entities_json=?,
                    caption=?,
                    caption_entities_json=?,
                    caption_above=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    chat_id,
                    kind,
                    normalized_text,
                    normalized_entities,
                    normalized_caption,
                    normalized_caption_entities,
                    normalized_caption_above,
                    now,
                    draft_id,
                ),
            )
            if cur.rowcount != 1:
                await self._conn.rollback()
                return False

            await self._conn.execute("DELETE FROM draft_media WHERE draft_id=?", (draft_id,))
            for idx, item in enumerate(items):
                await self._conn.execute(
                    "INSERT INTO draft_media(draft_id, idx, type, file_id) VALUES(?, ?, ?, ?)",
                    (draft_id, idx, item["type"], item["file_id"]),
                )
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

        return True

    async def delete_draft(self, draft_id: str, user_id: int) -> bool:
        permissions = await self.get_draft_permissions(draft_id, user_id)
        if permissions is None or not permissions.can_delete:
            return False

        cur = await self._conn.execute("DELETE FROM drafts WHERE id=?", (draft_id,))
        await self._conn.commit()
        return cur.rowcount == 1

    async def create_scheduled_text_post(
        self,
        user_id: int,
        chat_id: int,
        scheduled_at_utc: int,
        text: str,
        entities_json: str | None,
    ) -> str:
        post_id = uuid.uuid4().hex
        await self._insert_scheduled_text_post(
            post_id=post_id,
            user_id=user_id,
            chat_id=chat_id,
            scheduled_at_utc=scheduled_at_utc,
            text=text,
            entities_json=entities_json,
            created_at=int(time.time()),
        )
        await self._conn.commit()
        return post_id

    async def create_scheduled_media_post(
        self,
        user_id: int,
        chat_id: int,
        scheduled_at_utc: int,
        caption: str | None,
        caption_entities_json: str | None,
        caption_above: bool | None,
        media_items: list[dict[str, str]],
    ) -> str:
        post_id = uuid.uuid4().hex
        await self._insert_scheduled_media_post(
            post_id=post_id,
            user_id=user_id,
            chat_id=chat_id,
            scheduled_at_utc=scheduled_at_utc,
            caption=caption,
            caption_entities_json=caption_entities_json,
            caption_above=caption_above,
            media_items=media_items,
            created_at=int(time.time()),
        )
        await self._conn.commit()
        return post_id

    async def create_broadcast_posts(
        self,
        *,
        user_id: int,
        chat_ids: Iterable[int],
        scheduled_at_utc: int,
        kind: str,
        text: str | None = None,
        entities_json: str | None = None,
        caption: str | None = None,
        caption_entities_json: str | None = None,
        caption_above: bool | None = None,
        media_items: list[dict[str, str]] | None = None,
    ) -> list[str]:
        unique_chat_ids = list(dict.fromkeys(chat_ids))
        if not unique_chat_ids:
            return []
        if kind not in {"text", "media"}:
            raise ValueError("kind must be 'text' or 'media'")
        if kind == "media" and not media_items:
            raise ValueError("media_items are required for media broadcast")

        created_at = int(time.time())
        post_ids: list[str] = []
        try:
            for chat_id in unique_chat_ids:
                post_id = uuid.uuid4().hex
                if kind == "text":
                    await self._insert_scheduled_text_post(
                        post_id=post_id,
                        user_id=user_id,
                        chat_id=chat_id,
                        scheduled_at_utc=scheduled_at_utc,
                        text=str(text or ""),
                        entities_json=entities_json,
                        created_at=created_at,
                    )
                else:
                    await self._insert_scheduled_media_post(
                        post_id=post_id,
                        user_id=user_id,
                        chat_id=chat_id,
                        scheduled_at_utc=scheduled_at_utc,
                        caption=caption,
                        caption_entities_json=caption_entities_json,
                        caption_above=caption_above,
                        media_items=list(media_items or []),
                        created_at=created_at,
                    )
                post_ids.append(post_id)
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise
        return post_ids

    async def _insert_scheduled_text_post(
        self,
        *,
        post_id: str,
        user_id: int,
        chat_id: int,
        scheduled_at_utc: int,
        text: str,
        entities_json: str | None,
        created_at: int,
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO scheduled_posts(
                id, user_id, chat_id, scheduled_at_utc, status, kind, text, entities_json,
                attempts, next_retry_at_utc, created_at
            ) VALUES(?, ?, ?, ?, 'pending', 'text', ?, ?, 0, NULL, ?)
            """,
            (post_id, user_id, chat_id, scheduled_at_utc, text, entities_json, created_at),
        )

    async def _insert_scheduled_media_post(
        self,
        *,
        post_id: str,
        user_id: int,
        chat_id: int,
        scheduled_at_utc: int,
        caption: str | None,
        caption_entities_json: str | None,
        caption_above: bool | None,
        media_items: list[dict[str, str]],
        created_at: int,
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO scheduled_posts(
                id, user_id, chat_id, scheduled_at_utc, status, kind,
                caption, caption_entities_json, caption_above,
                attempts, next_retry_at_utc, created_at
            ) VALUES(?, ?, ?, ?, 'pending', 'media', ?, ?, ?, 0, NULL, ?)
            """,
            (
                post_id,
                user_id,
                chat_id,
                scheduled_at_utc,
                caption,
                caption_entities_json,
                None if caption_above is None else int(caption_above),
                created_at,
            ),
        )
        for idx, item in enumerate(media_items):
            await self._conn.execute(
                "INSERT INTO scheduled_post_media(post_id, idx, type, file_id) VALUES(?, ?, ?, ?)",
                (post_id, idx, str(item["type"]), str(item["file_id"])),
            )

    @staticmethod
    def _normalize_scheduled_payload(
        *,
        kind: str,
        text: str | None,
        entities_json: str | None,
        caption: str | None,
        caption_entities_json: str | None,
        caption_above: bool | None,
        media_items: list[dict[str, str]] | None,
    ) -> tuple[str | None, str | None, str | None, str | None, int | None, list[dict[str, str]]]:
        return StateStore._normalize_draft_payload(
            kind=kind,
            text=text,
            entities_json=entities_json,
            caption=caption,
            caption_entities_json=caption_entities_json,
            caption_above=caption_above,
            media_items=media_items,
        )

    async def list_pending_posts(self, user_id: int, limit: int = 10) -> list[ScheduledPostRow]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT *
            FROM scheduled_posts
            WHERE user_id=? AND status='pending'
            ORDER BY scheduled_at_utc ASC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [self._row_to_post(r) for r in rows]

    async def list_editable_pending_posts(self, user_id: int, limit: int = 10) -> list[ScheduledPostRow]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT sp.*
            FROM scheduled_posts sp
            LEFT JOIN recurring_instances ri ON ri.post_id = sp.id
            WHERE sp.user_id=?
              AND sp.status='pending'
              AND ri.post_id IS NULL
            ORDER BY sp.scheduled_at_utc ASC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [self._row_to_post(r) for r in rows]

    async def get_scheduled_post(self, post_id: str) -> ScheduledPostRow | None:
        row = await self._execute_fetchone(
            "SELECT * FROM scheduled_posts WHERE id=?",
            (post_id,),
        )
        return None if row is None else self._row_to_post(row)

    async def update_editable_post_time(self, post_id: str, user_id: int, *, scheduled_at_utc: int) -> bool:
        cur = await self._conn.execute(
            """
            UPDATE scheduled_posts
            SET scheduled_at_utc=?
            WHERE id=?
              AND user_id=?
              AND status='pending'
              AND NOT EXISTS (
                  SELECT 1
                  FROM recurring_instances
                  WHERE post_id=?
              )
            """,
            (scheduled_at_utc, post_id, user_id, post_id),
        )
        await self._conn.commit()
        return cur.rowcount == 1

    async def update_editable_post_content(
        self,
        post_id: str,
        user_id: int,
        *,
        kind: str,
        text: str | None = None,
        entities_json: str | None = None,
        caption: str | None = None,
        caption_entities_json: str | None = None,
        caption_above: bool | None = None,
        media_items: list[dict[str, str]] | None = None,
    ) -> bool:
        normalized_text, normalized_entities, normalized_caption, normalized_caption_entities, normalized_caption_above, items = (
            self._normalize_scheduled_payload(
                kind=kind,
                text=text,
                entities_json=entities_json,
                caption=caption,
                caption_entities_json=caption_entities_json,
                caption_above=caption_above,
                media_items=media_items,
            )
        )

        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            cur = await self._conn.execute(
                """
                UPDATE scheduled_posts
                SET kind=?,
                    text=?,
                    entities_json=?,
                    caption=?,
                    caption_entities_json=?,
                    caption_above=?
                WHERE id=?
                  AND user_id=?
                  AND status='pending'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM recurring_instances
                      WHERE post_id=?
                  )
                """,
                (
                    kind,
                    normalized_text,
                    normalized_entities,
                    normalized_caption,
                    normalized_caption_entities,
                    normalized_caption_above,
                    post_id,
                    user_id,
                    post_id,
                ),
            )
            if cur.rowcount != 1:
                await self._conn.rollback()
                return False

            await self._conn.execute("DELETE FROM scheduled_post_media WHERE post_id=?", (post_id,))
            for idx, item in enumerate(items):
                await self._conn.execute(
                    "INSERT INTO scheduled_post_media(post_id, idx, type, file_id) VALUES(?, ?, ?, ?)",
                    (post_id, idx, str(item["type"]), str(item["file_id"])),
                )

            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

        return True

    async def cancel_post(self, user_id: int, post_id: str) -> bool:
        cur = await self._conn.execute(
            """
            UPDATE scheduled_posts
            SET status='cancelled'
            WHERE id=? AND user_id=? AND status='pending'
            """,
            (post_id, user_id),
        )
        await self._conn.commit()
        return cur.rowcount == 1

    async def list_due_posts(self, now_utc: int, limit: int = 10) -> list[ScheduledPostRow]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT *
            FROM scheduled_posts
            WHERE status='pending'
              AND scheduled_at_utc <= ?
              AND (next_retry_at_utc IS NULL OR next_retry_at_utc <= ?)
            ORDER BY scheduled_at_utc ASC
            LIMIT ?
            """,
            (now_utc, now_utc, limit),
        )
        return [self._row_to_post(r) for r in rows]

    async def claim_post_for_sending(self, post_id: str, now_utc: int) -> bool:
        cur = await self._conn.execute(
            """
            UPDATE scheduled_posts
            SET status='sending', attempts=attempts+1, last_error=NULL
            WHERE id=?
              AND status='pending'
              AND (next_retry_at_utc IS NULL OR next_retry_at_utc <= ?)
            """,
            (post_id, now_utc),
        )
        await self._conn.commit()
        return cur.rowcount == 1

    async def mark_sent(self, post_id: str, sent_at_utc: int) -> None:
        await self._conn.execute(
            """
            UPDATE scheduled_posts
            SET status='sent', sent_at=?, next_retry_at_utc=NULL
            WHERE id=? AND status='sending'
            """,
            (sent_at_utc, post_id),
        )
        await self._conn.commit()

    async def mark_retry(self, post_id: str, next_retry_at_utc: int, error: str) -> None:
        await self._conn.execute(
            """
            UPDATE scheduled_posts
            SET status='pending', next_retry_at_utc=?, last_error=?
            WHERE id=? AND status='sending'
            """,
            (next_retry_at_utc, error, post_id),
        )
        await self._conn.commit()

    async def mark_failed(self, post_id: str, error: str) -> None:
        await self._conn.execute(
            """
            UPDATE scheduled_posts
            SET status='failed', last_error=?
            WHERE id=? AND status='sending'
            """,
            (error, post_id),
        )
        await self._conn.commit()

    async def get_post_media(self, post_id: str) -> list[dict[str, str]]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT idx, type, file_id
            FROM scheduled_post_media
            WHERE post_id=?
            ORDER BY idx ASC
            """,
            (post_id,),
        )
        out: list[dict[str, str]] = []
        for r in rows:
            out.append({"type": str(r["type"]), "file_id": str(r["file_id"])})
        return out

    async def get_destination_title(self, chat_id: int) -> str | None:
        row = await self._execute_fetchone(
            "SELECT title FROM destinations WHERE chat_id=?",
            (chat_id,),
        )
        return None if row is None else str(row["title"])

    async def create_recurring_pattern(
        self,
        user_id: int,
        chat_id: int,
        interval_type: str,
        time_of_day_minutes: int,
        timezone: str,
        start_at_utc: int,
        *,
        weekdays_mask: int | None = None,
        end_at_utc: int | None = None,
        max_occurrences: int | None = None,
        current_count: int = 0,
        is_active: bool = True,
    ) -> str:
        now = int(time.time())
        pattern_id = uuid.uuid4().hex
        await self._conn.execute(
            """
            INSERT INTO recurring_patterns(
                id,
                user_id,
                chat_id,
                interval_type,
                weekdays_mask,
                time_of_day_minutes,
                timezone,
                start_at_utc,
                end_at_utc,
                max_occurrences,
                current_count,
                is_active,
                created_at,
                updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pattern_id,
                user_id,
                chat_id,
                interval_type,
                weekdays_mask,
                time_of_day_minutes,
                timezone,
                start_at_utc,
                end_at_utc,
                max_occurrences,
                current_count,
                int(is_active),
                now,
                now,
            ),
        )
        await self._conn.commit()
        return pattern_id

    async def get_recurring_pattern(self, pattern_id: str) -> RecurringPattern | None:
        row = await self._execute_fetchone(
            "SELECT * FROM recurring_patterns WHERE id=?",
            (pattern_id,),
        )
        return None if row is None else self._row_to_recurring_pattern(row)

    async def list_user_recurring(self, user_id: int, include_inactive: bool = False) -> list[RecurringPattern]:
        query = """
            SELECT *
            FROM recurring_patterns
            WHERE user_id=?
        """
        params: list[object] = [user_id]
        if not include_inactive:
            query += " AND is_active=1"
        query += " ORDER BY created_at DESC, id ASC"

        rows = await self._conn.execute_fetchall(query, tuple(params))
        return [self._row_to_recurring_pattern(row) for row in rows]

    async def list_user_recurring_summaries(
        self,
        user_id: int,
        *,
        offset: int = 0,
        limit: int = 10,
        include_inactive: bool = False,
    ) -> list[RecurringPatternSummary]:
        query = """
            SELECT
                rp.*,
                d.title AS destination_title,
                d.username AS destination_username,
                (
                    SELECT sp.id
                    FROM recurring_instances ri
                    JOIN scheduled_posts sp ON sp.id = ri.post_id
                    WHERE ri.pattern_id = rp.id
                      AND sp.status IN ('pending', 'sending')
                    ORDER BY
                        CASE sp.status WHEN 'pending' THEN 0 ELSE 1 END,
                        ri.ordinal ASC
                    LIMIT 1
                ) AS next_post_id,
                (
                    SELECT sp.scheduled_at_utc
                    FROM recurring_instances ri
                    JOIN scheduled_posts sp ON sp.id = ri.post_id
                    WHERE ri.pattern_id = rp.id
                      AND sp.status IN ('pending', 'sending')
                    ORDER BY
                        CASE sp.status WHEN 'pending' THEN 0 ELSE 1 END,
                        ri.ordinal ASC
                    LIMIT 1
                ) AS next_scheduled_at_utc,
                (
                    SELECT sp.status
                    FROM recurring_instances ri
                    JOIN scheduled_posts sp ON sp.id = ri.post_id
                    WHERE ri.pattern_id = rp.id
                      AND sp.status IN ('pending', 'sending')
                    ORDER BY
                        CASE sp.status WHEN 'pending' THEN 0 ELSE 1 END,
                        ri.ordinal ASC
                    LIMIT 1
                ) AS next_post_status
            FROM recurring_patterns rp
            JOIN destinations d ON d.chat_id = rp.chat_id
            WHERE rp.user_id=?
        """
        params: list[object] = [user_id]
        if not include_inactive:
            query += " AND rp.is_active=1"
        query += " ORDER BY rp.created_at DESC, rp.id ASC LIMIT ? OFFSET ?"
        params.extend((limit, offset))

        rows = await self._conn.execute_fetchall(query, tuple(params))
        return [self._row_to_recurring_summary(row) for row in rows]

    async def update_recurring_count(self, pattern_id: str, new_count: int) -> bool:
        now = int(time.time())
        cur = await self._conn.execute(
            """
            UPDATE recurring_patterns
            SET current_count=?, updated_at=?
            WHERE id=?
            """,
            (new_count, now, pattern_id),
        )
        await self._conn.commit()
        return cur.rowcount == 1

    async def delete_recurring_pattern(self, pattern_id: str) -> bool:
        now = int(time.time())
        cur = await self._conn.execute(
            """
            UPDATE recurring_patterns
            SET is_active=0, updated_at=?
            WHERE id=? AND is_active=1
            """,
            (now, pattern_id),
        )
        await self._conn.commit()
        return cur.rowcount == 1

    async def cancel_recurring_pattern(self, user_id: int, pattern_id: str) -> bool:
        now = int(time.time())
        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = await self._execute_fetchone(
                "SELECT id FROM recurring_patterns WHERE id=? AND user_id=?",
                (pattern_id, user_id),
            )
            if row is None:
                await self._conn.rollback()
                return False

            await self._conn.execute(
                """
                UPDATE recurring_patterns
                SET is_active=0, updated_at=?
                WHERE id=? AND user_id=?
                """,
                (now, pattern_id, user_id),
            )
            await self._conn.execute(
                """
                UPDATE scheduled_posts
                SET status='cancelled'
                WHERE user_id=?
                  AND status='pending'
                  AND id IN (
                      SELECT post_id
                      FROM recurring_instances
                      WHERE pattern_id=?
                  )
                """,
                (user_id, pattern_id),
            )
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

        return True

    async def create_recurring_instance(
        self,
        pattern_id: str,
        post_id: str,
        ordinal: int,
        scheduled_for_utc: int,
    ) -> RecurringInstance:
        created_at = int(time.time())
        await self._conn.execute(
            """
            INSERT INTO recurring_instances(pattern_id, post_id, ordinal, scheduled_for_utc, created_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (pattern_id, post_id, ordinal, scheduled_for_utc, created_at),
        )
        await self._conn.commit()
        return RecurringInstance(
            pattern_id=pattern_id,
            post_id=post_id,
            ordinal=ordinal,
            scheduled_for_utc=scheduled_for_utc,
            created_at=created_at,
        )

    async def materialize_next_recurring_post(
        self,
        pattern_id: str,
        source_post_id: str,
        next_ordinal: int,
        scheduled_for_utc: int,
    ) -> RecurringInstance | None:
        source_post = await self.get_scheduled_post(source_post_id)
        if source_post is None:
            raise ValueError(f"Scheduled post {source_post_id} not found")

        media_items = await self.get_post_media(source_post_id) if source_post.kind == "media" else []
        now = int(time.time())
        next_post_id = uuid.uuid4().hex

        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            pattern_row = await self._execute_fetchone(
                "SELECT is_active FROM recurring_patterns WHERE id=?",
                (pattern_id,),
            )
            if pattern_row is None or not bool(int(pattern_row["is_active"])):
                await self._conn.rollback()
                return None

            await self._conn.execute(
                """
                INSERT INTO scheduled_posts(
                    id, user_id, chat_id, scheduled_at_utc, status, kind, text, entities_json,
                    caption, caption_entities_json, caption_above,
                    attempts, next_retry_at_utc, created_at
                ) VALUES(?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, 0, NULL, ?)
                """,
                (
                    next_post_id,
                    source_post.user_id,
                    source_post.chat_id,
                    scheduled_for_utc,
                    source_post.kind,
                    source_post.text,
                    source_post.entities_json,
                    source_post.caption,
                    source_post.caption_entities_json,
                    source_post.caption_above,
                    now,
                ),
            )
            for idx, item in enumerate(media_items):
                await self._conn.execute(
                    "INSERT INTO scheduled_post_media(post_id, idx, type, file_id) VALUES(?, ?, ?, ?)",
                    (next_post_id, idx, item["type"], item["file_id"]),
                )

            await self._conn.execute(
                """
                INSERT INTO recurring_instances(pattern_id, post_id, ordinal, scheduled_for_utc, created_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (pattern_id, next_post_id, next_ordinal, scheduled_for_utc, now),
            )
            cur = await self._conn.execute(
                """
                UPDATE recurring_patterns
                SET current_count=?, updated_at=?
                WHERE id=? AND is_active=1
                """,
                (next_ordinal, now, pattern_id),
            )
            if cur.rowcount != 1:
                raise ValueError(f"Recurring pattern {pattern_id} is missing or inactive")

            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

        return RecurringInstance(
            pattern_id=pattern_id,
            post_id=next_post_id,
            ordinal=next_ordinal,
            scheduled_for_utc=scheduled_for_utc,
            created_at=now,
        )

    async def create_recurring_series(
        self,
        *,
        user_id: int,
        chat_id: int,
        interval_type: str,
        time_of_day_minutes: int,
        timezone: str,
        start_at_utc: int,
        kind: str,
        weekdays_mask: int | None = None,
        end_at_utc: int | None = None,
        max_occurrences: int | None = None,
        text: str | None = None,
        entities_json: str | None = None,
        caption: str | None = None,
        caption_entities_json: str | None = None,
        caption_above: bool | None = None,
        media_items: list[dict[str, str]] | None = None,
    ) -> tuple[str, str]:
        if kind not in {"text", "media"}:
            raise ValueError(f"Unsupported recurring post kind: {kind}")

        items = list(media_items or [])
        if kind == "media" and not items:
            raise ValueError("Recurring media post must contain at least one media item")

        now = int(time.time())
        pattern_id = uuid.uuid4().hex
        post_id = uuid.uuid4().hex

        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            await self._conn.execute(
                """
                INSERT INTO recurring_patterns(
                    id,
                    user_id,
                    chat_id,
                    interval_type,
                    weekdays_mask,
                    time_of_day_minutes,
                    timezone,
                    start_at_utc,
                    end_at_utc,
                    max_occurrences,
                    current_count,
                    is_active,
                    created_at,
                    updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?)
                """,
                (
                    pattern_id,
                    user_id,
                    chat_id,
                    interval_type,
                    weekdays_mask,
                    time_of_day_minutes,
                    timezone,
                    start_at_utc,
                    end_at_utc,
                    max_occurrences,
                    now,
                    now,
                ),
            )

            await self._conn.execute(
                """
                INSERT INTO scheduled_posts(
                    id, user_id, chat_id, scheduled_at_utc, status, kind, text, entities_json,
                    caption, caption_entities_json, caption_above,
                    attempts, next_retry_at_utc, created_at
                ) VALUES(?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, 0, NULL, ?)
                """,
                (
                    post_id,
                    user_id,
                    chat_id,
                    start_at_utc,
                    kind,
                    text,
                    entities_json,
                    caption,
                    caption_entities_json,
                    None if caption_above is None else int(caption_above),
                    now,
                ),
            )
            for idx, item in enumerate(items):
                await self._conn.execute(
                    "INSERT INTO scheduled_post_media(post_id, idx, type, file_id) VALUES(?, ?, ?, ?)",
                    (post_id, idx, item["type"], item["file_id"]),
                )

            await self._conn.execute(
                """
                INSERT INTO recurring_instances(pattern_id, post_id, ordinal, scheduled_for_utc, created_at)
                VALUES(?, ?, 1, ?, ?)
                """,
                (pattern_id, post_id, start_at_utc, now),
            )
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

        return pattern_id, post_id

    async def get_recurring_instance_by_post_id(self, post_id: str) -> RecurringInstance | None:
        row = await self._execute_fetchone(
            "SELECT * FROM recurring_instances WHERE post_id=?",
            (post_id,),
        )
        return None if row is None else self._row_to_recurring_instance(row)

    async def get_due_recurring_instances(self, now_utc: int, limit: int = 10) -> list[RecurringInstance]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT ri.*
            FROM recurring_instances ri
            JOIN recurring_patterns rp ON rp.id = ri.pattern_id
            JOIN scheduled_posts sp ON sp.id = ri.post_id
            WHERE rp.is_active=1
              AND ri.scheduled_for_utc <= ?
              AND sp.status='pending'
              AND sp.scheduled_at_utc <= ?
              AND (sp.next_retry_at_utc IS NULL OR sp.next_retry_at_utc <= ?)
            ORDER BY ri.scheduled_for_utc ASC, ri.ordinal ASC
            LIMIT ?
            """,
            (now_utc, now_utc, now_utc, limit),
        )
        return [self._row_to_recurring_instance(row) for row in rows]

    @staticmethod
    def dump_entities(entities: Iterable[Any] | None) -> str | None:
        if not entities:
            return None
        return json.dumps([e.model_dump() for e in entities], ensure_ascii=False)

    @staticmethod
    def _row_to_post(row: aiosqlite.Row) -> ScheduledPostRow:
        return ScheduledPostRow(
            id=str(row["id"]),
            user_id=int(row["user_id"]),
            chat_id=int(row["chat_id"]),
            scheduled_at_utc=int(row["scheduled_at_utc"]),
            status=str(row["status"]),
            kind=str(row["kind"]),
            text=row["text"],
            entities_json=row["entities_json"],
            caption=row["caption"],
            caption_entities_json=row["caption_entities_json"],
            caption_above=row["caption_above"],
            attempts=int(row["attempts"]),
            next_retry_at_utc=row["next_retry_at_utc"],
            created_at=int(row["created_at"]),
            sent_at=row["sent_at"],
            last_error=row["last_error"],
        )

    @staticmethod
    def _row_to_team(row: aiosqlite.Row) -> Team:
        return Team(
            id=str(row["id"]),
            owner_user_id=int(row["owner_user_id"]),
            name=str(row["name"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )

    @staticmethod
    def _row_to_team_member(row: aiosqlite.Row) -> TeamMember:
        return TeamMember(
            team_id=str(row["team_id"]),
            user_id=int(row["user_id"]),
            role=str(row["role"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )

    @staticmethod
    def _row_to_team_invite(row: aiosqlite.Row) -> TeamInvite:
        return TeamInvite(
            token=str(row["token"]),
            team_id=str(row["team_id"]),
            role=str(row["role"]),
            created_by_user_id=int(row["created_by_user_id"]),
            created_at=int(row["created_at"]),
            expires_at=int(row["expires_at"]),
            accepted_by_user_id=None if row["accepted_by_user_id"] is None else int(row["accepted_by_user_id"]),
            accepted_at=None if row["accepted_at"] is None else int(row["accepted_at"]),
        )

    @staticmethod
    def _row_to_draft(row: aiosqlite.Row) -> DraftRow:
        return DraftRow(
            id=str(row["id"]),
            team_id=None if row["team_id"] is None else str(row["team_id"]),
            author_user_id=int(row["author_user_id"]),
            chat_id=int(row["chat_id"]),
            kind=str(row["kind"]),
            text=row["text"],
            entities_json=row["entities_json"],
            caption=row["caption"],
            caption_entities_json=row["caption_entities_json"],
            caption_above=row["caption_above"],
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )

    @staticmethod
    def _row_to_recurring_pattern(row: aiosqlite.Row) -> RecurringPattern:
        return RecurringPattern(
            id=str(row["id"]),
            user_id=int(row["user_id"]),
            chat_id=int(row["chat_id"]),
            interval_type=str(row["interval_type"]),
            weekdays_mask=None if row["weekdays_mask"] is None else int(row["weekdays_mask"]),
            time_of_day_minutes=int(row["time_of_day_minutes"]),
            timezone=str(row["timezone"]),
            start_at_utc=int(row["start_at_utc"]),
            end_at_utc=None if row["end_at_utc"] is None else int(row["end_at_utc"]),
            max_occurrences=None if row["max_occurrences"] is None else int(row["max_occurrences"]),
            current_count=int(row["current_count"]),
            is_active=bool(int(row["is_active"])),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )

    @staticmethod
    def _row_to_recurring_instance(row: aiosqlite.Row) -> RecurringInstance:
        return RecurringInstance(
            pattern_id=str(row["pattern_id"]),
            post_id=str(row["post_id"]),
            ordinal=int(row["ordinal"]),
            scheduled_for_utc=int(row["scheduled_for_utc"]),
            created_at=int(row["created_at"]),
        )

    @classmethod
    def _row_to_recurring_summary(cls, row: aiosqlite.Row) -> RecurringPatternSummary:
        return RecurringPatternSummary(
            pattern=cls._row_to_recurring_pattern(row),
            destination_title=str(row["destination_title"]),
            destination_username=row["destination_username"],
            next_post_id=None if row["next_post_id"] is None else str(row["next_post_id"]),
            next_scheduled_at_utc=(
                None if row["next_scheduled_at_utc"] is None else int(row["next_scheduled_at_utc"])
            ),
            next_post_status=None if row["next_post_status"] is None else str(row["next_post_status"]),
        )
