from __future__ import annotations

from pathlib import Path

CADDYFILE = Path(__file__).resolve().parent.parent / "deploy" / "Caddyfile"


def test_caddyfile_sets_security_headers() -> None:
    # Text-presence check only (not a live-proxy test): assert the hardening
    # directives are declared. Confirms the header block is not dropped.
    text = CADDYFILE.read_text(encoding="utf-8")
    assert "X-Content-Type-Options" in text
    assert "nosniff" in text
    assert "Content-Security-Policy" in text
