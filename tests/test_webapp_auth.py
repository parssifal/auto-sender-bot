from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest

from core.webapp import validate_init_data

TOKEN = "123456:test-token"
NOW = 1_800_000_000


def _sign(fields: dict[str, str], token: str) -> str:
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    signed = dict(fields)
    signed["hash"] = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(signed)


def _init_data(*, token: str = TOKEN, user: dict | None = None, auth_date: int = NOW) -> str:
    if user is None:
        user = {"id": 42, "first_name": "Ann", "username": "ann"}
    fields = {
        "auth_date": str(auth_date),
        "query_id": "AAA",
        "user": json.dumps(user, separators=(",", ":")),
    }
    return _sign(fields, token)


def test_valid_init_data_returns_user() -> None:
    result = validate_init_data(_init_data(), TOKEN, max_age_s=3600, now=NOW)
    assert result is not None
    assert result["id"] == 42


def test_tampered_hash_rejected() -> None:
    raw = _init_data()
    tampered = raw[:-1] + ("0" if raw[-1] != "0" else "1")
    assert validate_init_data(tampered, TOKEN, max_age_s=3600, now=NOW) is None


def test_wrong_token_rejected() -> None:
    raw = _init_data(token="999:other-token")
    assert validate_init_data(raw, TOKEN, max_age_s=3600, now=NOW) is None


def test_stale_auth_date_rejected() -> None:
    raw = _init_data(auth_date=NOW - 7200)  # 2h old
    assert validate_init_data(raw, TOKEN, max_age_s=3600, now=NOW) is None


def test_fresh_auth_date_within_window_accepted() -> None:
    raw = _init_data(auth_date=NOW - 1800)  # 30m old
    result = validate_init_data(raw, TOKEN, max_age_s=3600, now=NOW)
    assert result is not None


def test_garbage_init_data_rejected() -> None:
    assert validate_init_data("not a query string %%%", TOKEN, max_age_s=3600, now=NOW) is None
