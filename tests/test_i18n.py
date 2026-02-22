from telegram.i18n import (
    key_values,
    normalize_language,
    resolve_language_choice,
    resolve_timezone_choice,
    timezone_choice_rows,
    tr,
)


def test_normalize_language_defaults_to_english() -> None:
    assert normalize_language(None) == "en"
    assert normalize_language("de") == "de"
    assert normalize_language("ru-RU") == "ru"


def test_language_choice_resolution() -> None:
    assert resolve_language_choice("en") == "en"
    assert resolve_language_choice("English") == "en"
    assert resolve_language_choice("Русский") == "ru"
    assert resolve_language_choice("Українська") == "uk"
    assert resolve_language_choice("Deutsch") == "de"
    assert resolve_language_choice("العربية") == "ar"
    assert resolve_language_choice("हिन्दी") == "hi"
    assert resolve_language_choice("中文") == "zh"
    assert resolve_language_choice("日本語") == "ja"
    assert resolve_language_choice("fr") is None


def test_timezone_choices_are_localized() -> None:
    en_rows = timezone_choice_rows("en")
    ru_rows = timezone_choice_rows("ru")
    zh_rows = timezone_choice_rows("zh")
    assert any("Moscow (UTC+3)" in row for row in en_rows)
    assert any("Москва (UTC+3)" in row for row in ru_rows)
    assert any("基辅 (UTC+2)" in row for row in zh_rows)


def test_timezone_choice_resolution() -> None:
    assert resolve_timezone_choice("Moscow (UTC+3)") == "Europe/Moscow"
    assert resolve_timezone_choice("Москва (UTC+3)") == "Europe/Moscow"
    assert resolve_timezone_choice("كييف (UTC+2)") == "Europe/Kyiv"
    assert resolve_timezone_choice("東京 (UTC+9)") == "Asia/Tokyo"
    assert resolve_timezone_choice("Unknown City") is None


def test_menu_key_values_contains_languages() -> None:
    values = key_values("menu_schedule")
    assert "Schedule" in values
    assert "Запланировать" in values
    assert "计划" in values
    assert "予約" in values
    assert tr("en", "menu_language") == "Language"
    assert tr("ru", "menu_language") == "Язык"
