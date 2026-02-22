from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


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


def parse_local_datetime(text: str, tz_name: str) -> ParsedScheduleTime:
    # Expected format: "DD.MM.YYYY HH:MM"
    raw = (text or "").strip()
    dt_naive = datetime.strptime(raw, "%d.%m.%Y %H:%M")
    tz = ZoneInfo(tz_name)
    local_dt = dt_naive.replace(tzinfo=tz)
    utc_dt = local_dt.astimezone(timezone.utc)
    return ParsedScheduleTime(local_dt=local_dt, utc_epoch=int(utc_dt.timestamp()))
