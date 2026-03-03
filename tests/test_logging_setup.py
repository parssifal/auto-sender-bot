from __future__ import annotations

import json
import logging
from io import StringIO

from core.logging_setup import configure_logging


def test_configure_logging_emits_json_for_stdlib_logger() -> None:
    stream = StringIO()
    configure_logging("INFO", stream=stream)

    logging.getLogger("tests.logging").info("structured message")

    payload = json.loads(stream.getvalue().strip())
    assert payload["event"] == "structured message"
    assert payload["level"] == "info"
    assert payload["logger"] == "tests.logging"
    assert "timestamp" in payload


def test_configure_logging_formats_stdlib_positional_arguments() -> None:
    stream = StringIO()
    configure_logging("INFO", stream=stream)

    logging.getLogger("tests.logging").warning("post %s retried", "abc123")

    payload = json.loads(stream.getvalue().strip())
    assert payload["event"] == "post abc123 retried"
    assert payload["level"] == "warning"
