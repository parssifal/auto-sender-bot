from __future__ import annotations

from dataclasses import dataclass


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
