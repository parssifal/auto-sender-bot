from core.utils import split_text


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
