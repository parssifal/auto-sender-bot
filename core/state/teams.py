from __future__ import annotations

import time
import uuid

from core.state.base import locked_write
from core.state.models import Team, TeamInvite, TeamInviteAcceptance, TeamMember


class TeamsMixin:
    @locked_write
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

    @locked_write
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

    @locked_write
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

    @locked_write
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
