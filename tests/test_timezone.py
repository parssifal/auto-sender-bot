from core.timezone_resolver import timezone_from_coordinates
from telegram.router import _is_valid_tz_name, _timezone_setup_kb


def test_timezone_from_coordinates_moscow() -> None:
    tz_name = timezone_from_coordinates(latitude=55.7558, longitude=37.6176)
    assert tz_name == "Europe/Moscow"


def test_timezone_from_coordinates_invalid_bounds() -> None:
    assert timezone_from_coordinates(latitude=120.0, longitude=37.6) is None
    assert timezone_from_coordinates(latitude=55.7, longitude=220.0) is None


def test_timezone_setup_keyboard_requests_location() -> None:
    kb = _timezone_setup_kb()
    assert kb.keyboard
    assert kb.keyboard[0]
    assert kb.keyboard[0][0].request_location is True


def test_is_valid_tz_name() -> None:
    assert _is_valid_tz_name("Europe/Moscow") is True
    assert _is_valid_tz_name("Not/A_Real_TZ") is False
