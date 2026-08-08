import pytest

from core.utils import (
    MIN_SCHEDULE_LEAD_SECONDS,
    NonexistentLocalTimeError,
    parse_local_datetime,
    split_text,
    validate_schedule_time,
)


def test_split_text_progress_no_newlines() -> None:
    text = "a" * 10000
    chunks = split_text(text, max_len=4096)
    assert "".join(chunks) == text
    assert all(1 <= len(c) <= 4096 for c in chunks)


def test_split_text_prefers_newlines() -> None:
    text = "hello\n" + ("x" * 5000)
    chunks = split_text(text, max_len=4096)
    assert chunks[0] == "hello"
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_validate_schedule_time_rejects_past_timestamp() -> None:
    result = validate_schedule_time(100, now_utc=100)

    assert not result.is_valid
    assert result.error_key == "datetime_future_required"


def test_validate_schedule_time_rejects_too_soon_timestamp() -> None:
    result = validate_schedule_time(100 + MIN_SCHEDULE_LEAD_SECONDS - 1, now_utc=100)

    assert not result.is_valid
    assert result.error_key == "datetime_min_lead_required"


def test_validate_schedule_time_accepts_minimum_lead_time_boundary() -> None:
    result = validate_schedule_time(100 + MIN_SCHEDULE_LEAD_SECONDS, now_utc=100)

    assert result.is_valid
    assert result.error_key is None


def test_parse_local_datetime_rejects_nonexistent_dst_gap() -> None:
    # T-07: 02:30 on 2026-03-29 does not exist in Europe/Berlin (clocks jump 02:00 -> 03:00).
    # The old code silently returned 02:30+01:00 while the post fired at 03:30 wall time.
    with pytest.raises(NonexistentLocalTimeError):
        parse_local_datetime("29.03.2026 02:30", "Europe/Berlin")


def test_parse_local_datetime_accepts_normal_time() -> None:
    parsed = parse_local_datetime("15.06.2026 12:00", "Europe/Berlin")

    assert parsed.local_dt.isoformat() == "2026-06-15T12:00:00+02:00"


def test_parse_local_datetime_keeps_first_occurrence_of_ambiguous_time() -> None:
    # T-07 (product decision): fall-back 02:30 on 2026-10-25 exists twice; keep the
    # first occurrence (fold=0, CEST +02:00) — current behaviour, NOT rejected.
    parsed = parse_local_datetime("25.10.2026 02:30", "Europe/Berlin")

    assert parsed.local_dt.isoformat() == "2026-10-25T02:30:00+02:00"
