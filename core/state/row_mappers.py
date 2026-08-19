from __future__ import annotations

import json
from typing import Any, Iterable

import aiosqlite

from core.state.models import (
    DraftRow,
    RecurringInstance,
    RecurringPattern,
    RecurringPatternSummary,
    ScheduledPostRow,
    Team,
    TeamInvite,
    TeamMember,
)


class RowMappersMixin:
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
            reaction_emojis_json=row["reaction_emojis_json"],
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
