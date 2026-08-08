from __future__ import annotations

from dataclasses import dataclass
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

MIN_SCHEDULE_LEAD_SECONDS = 5 * 60


class NonexistentLocalTimeError(ValueError):
    """Local wall-clock time that does not exist because of a DST spring-forward gap."""


def split_text(text: str, max_len: int) -> list[str]:
    if max_len <= 0:
        raise ValueError("max_len must be > 0")

    remaining = text or ""
    chunks: list[str] = []
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        cut = remaining.rfind("\n", 0, max_len + 1)
        if cut <= 0:
            cut = remaining.rfind(" ", 0, max_len + 1)
        if cut <= 0:
            cut = max_len

        chunk = remaining[:cut]
        remaining = remaining[cut:]
        if remaining.startswith("\n") or remaining.startswith(" "):
            remaining = remaining[1:]

        chunks.append(chunk)

    return chunks


@dataclass(frozen=True)
class ParsedScheduleTime:
    local_dt: datetime
    utc_epoch: int


@dataclass(frozen=True)
class ScheduleTimeValidation:
    is_valid: bool
    error_key: str | None = None


def reject_nonexistent_local_time(local_dt: datetime) -> None:
    # A DST spring-forward gap makes some wall-clock times non-existent: converting to UTC
    # and back does NOT round-trip. Ambiguous (fall-back) times DO round-trip and pass here.
    if local_dt.astimezone(timezone.utc).astimezone(local_dt.tzinfo) != local_dt:
        raise NonexistentLocalTimeError(local_dt.strftime("%d.%m.%Y %H:%M"))


def parse_local_datetime(text: str, tz_name: str) -> ParsedScheduleTime:
    # Expected format: "DD.MM.YYYY HH:MM"
    raw = (text or "").strip()
    dt_naive = datetime.strptime(raw, "%d.%m.%Y %H:%M")
    tz = ZoneInfo(tz_name)
    local_dt = dt_naive.replace(tzinfo=tz)
    reject_nonexistent_local_time(local_dt)
    utc_dt = local_dt.astimezone(timezone.utc)
    return ParsedScheduleTime(local_dt=local_dt, utc_epoch=int(utc_dt.timestamp()))


def validate_schedule_time(
    utc_timestamp: int,
    *,
    now_utc: int | None = None,
    min_lead_seconds: int = MIN_SCHEDULE_LEAD_SECONDS,
) -> ScheduleTimeValidation:
    if min_lead_seconds < 0:
        raise ValueError("min_lead_seconds must be >= 0")

    current_utc = int(time.time()) if now_utc is None else int(now_utc)
    if utc_timestamp <= current_utc:
        return ScheduleTimeValidation(is_valid=False, error_key="datetime_future_required")
    if utc_timestamp < current_utc + min_lead_seconds:
        return ScheduleTimeValidation(is_valid=False, error_key="datetime_min_lead_required")
    return ScheduleTimeValidation(is_valid=True)
