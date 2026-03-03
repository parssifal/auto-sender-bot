from core.utils import MIN_SCHEDULE_LEAD_SECONDS, split_text, validate_schedule_time


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
