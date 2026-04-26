from types import SimpleNamespace

from finage.telegram_bot import chunk_text, is_authorized


def make_update(user_id: int | None, chat_id: int | None):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id) if user_id is not None else None,
        effective_chat=SimpleNamespace(id=chat_id) if chat_id is not None else None,
    )


def test_is_authorized_accepts_allowed_user_or_chat() -> None:
    assert is_authorized(make_update(123, 999), {123})
    assert is_authorized(make_update(111, 999), {999})


def test_is_authorized_rejects_empty_allowlist() -> None:
    assert not is_authorized(make_update(123, 999), set())


def test_chunk_text_splits_long_messages_on_newlines() -> None:
    text = "first line\n" + ("x" * 50) + "\nlast line"

    chunks = chunk_text(text, limit=30)

    assert len(chunks) > 1
    assert all(len(chunk) <= 50 for chunk in chunks)
