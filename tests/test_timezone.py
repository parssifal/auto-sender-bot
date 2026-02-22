from core.timezone_resolver import timezone_from_coordinates
from telegram.router import _is_valid_tz_name, _resolve_timezone_input, _timezone_setup_kb


def test_timezone_from_coordinates_moscow() -> None:
    tz_name = timezone_from_coordinates(latitude=55.7558, longitude=37.6176)
    assert tz_name == "Europe/Moscow"


def test_timezone_from_coordinates_invalid_bounds() -> None:
    assert timezone_from_coordinates(latitude=120.0, longitude=37.6) is None
    assert timezone_from_coordinates(latitude=55.7, longitude=220.0) is None


def test_timezone_setup_keyboard_requests_location() -> None:
    kb = _timezone_setup_kb("ru")
    assert kb.keyboard
    assert kb.keyboard[0]
    assert kb.keyboard[0][0].request_location is True
    flat_texts = [btn.text for row in kb.keyboard for btn in row]
    assert "Москва (UTC+3)" in flat_texts
    assert "Киев (UTC+2)" in flat_texts
    assert "Берлин (UTC+1)" in flat_texts
    assert "Нью-Йорк (UTC-5)" in flat_texts
    assert "Токио (UTC+9)" in flat_texts
    assert kb.one_time_keyboard is False
    assert kb.is_persistent is True


def test_is_valid_tz_name() -> None:
    assert _is_valid_tz_name("Europe/Moscow") is True
    assert _is_valid_tz_name("Not/A_Real_TZ") is False


def test_resolve_timezone_input() -> None:
    assert _resolve_timezone_input("Москва (UTC+3)") == "Europe/Moscow"
    assert _resolve_timezone_input("Киев (UTC+2)") == "Europe/Kyiv"
    assert _resolve_timezone_input("Moscow (UTC+3)") == "Europe/Moscow"
    assert _resolve_timezone_input("Tokyo (UTC+9)") == "Asia/Tokyo"
    assert _resolve_timezone_input("Europe/London") == "Europe/London"
    assert _resolve_timezone_input("Not/A_Real_TZ") is None
