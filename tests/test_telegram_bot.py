from types import SimpleNamespace

import pytest
from telegram.constants import ParseMode

from finage.telegram_bot import chunk_text, is_authorized, markdown_to_telegram_html, send_markdown_text


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


def test_markdown_to_telegram_html_formats_common_markdown() -> None:
    formatted = markdown_to_telegram_html("# **WSB Digest**\n- **TSLA**: `calls` <risk>")

    assert "<b>WSB Digest</b>" in formatted
    assert "<b>TSLA</b>" in formatted
    assert "<code>calls</code>" in formatted
    assert "&lt;risk&gt;" in formatted


class FakeBot:
    def __init__(self) -> None:
        self.messages = []

    async def send_message(self, **kwargs) -> None:
        self.messages.append(kwargs)


@pytest.mark.asyncio
async def test_send_markdown_text_uses_telegram_html_parse_mode() -> None:
    bot = FakeBot()

    await send_markdown_text(bot, 123, "**TSLA** momentum")

    assert bot.messages[0]["chat_id"] == 123
    assert bot.messages[0]["parse_mode"] == ParseMode.HTML
    assert bot.messages[0]["text"] == "<b>TSLA</b> momentum"
