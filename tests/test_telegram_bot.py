from types import SimpleNamespace

import pytest
from telegram.constants import ParseMode

from finage.settings import Settings
from finage.telegram_bot import (
    TelegramDigestBot,
    chunk_text,
    is_authorized,
    markdown_to_telegram_html,
    send_markdown_text,
)


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


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


def make_settings() -> Settings:
    return Settings(
        reddit_client_id="reddit-id",
        reddit_client_secret="reddit-secret",
        telegram_bot_token="telegram-token",
        telegram_allowed_ids=[123],
        telegram_default_chat_id=123,
        gemini_api_key="gemini-key",
    )


@pytest.mark.asyncio
async def test_help_lists_live_command() -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=999),
        effective_message=message,
    )
    context = SimpleNamespace(bot=FakeBot())

    await TelegramDigestBot(make_settings()).help(update, context)

    assert "/live" in message.replies[0]
    assert "/ticker <stock>" in message.replies[0]
    assert "/why <stock>" in message.replies[0]
    assert "/movers" in message.replies[0]
    assert "/health" in message.replies[0]


@pytest.mark.asyncio
async def test_ticker_requires_one_symbol_argument() -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=999),
        effective_message=message,
    )
    context = SimpleNamespace(bot=FakeBot(), args=[])

    await TelegramDigestBot(make_settings()).ticker(update, context)

    assert message.replies == ["Usage: /ticker TSLA"]


@pytest.mark.asyncio
async def test_ticker_rejects_invalid_symbol_before_scanning() -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=999),
        effective_message=message,
    )
    context = SimpleNamespace(bot=FakeBot(), args=["TSLA1"])

    await TelegramDigestBot(make_settings()).ticker(update, context)

    assert "Ticker must be 1-5 letters" in message.replies[0]


@pytest.mark.asyncio
async def test_ticker_sends_focused_markdown_brief(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeMomentumAnalysisService:
        def __init__(self, settings: Settings):
            self.settings = settings

        async def ticker(self, symbol: str) -> str:
            assert symbol == "TSLA"
            return "**TSLA Social Momentum**\n- focused brief"

    monkeypatch.setattr("finage.telegram_bot.MomentumAnalysisService", FakeMomentumAnalysisService)
    message = FakeMessage()
    bot = FakeBot()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=999),
        effective_message=message,
    )
    context = SimpleNamespace(bot=bot, args=["tsla"])

    await TelegramDigestBot(make_settings()).ticker(update, context)

    assert message.replies == ["Scanning social momentum for TSLA..."]
    assert bot.messages[0]["chat_id"] == 999
    assert "<b>TSLA Social Momentum</b>" in bot.messages[0]["text"]


@pytest.mark.asyncio
async def test_why_requires_one_symbol_argument() -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=999),
        effective_message=message,
    )
    context = SimpleNamespace(bot=FakeBot(), args=[])

    await TelegramDigestBot(make_settings()).why(update, context)

    assert message.replies == ["Usage: /why TSLA"]


@pytest.mark.asyncio
async def test_why_rejects_invalid_symbol_before_scanning() -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=999),
        effective_message=message,
    )
    context = SimpleNamespace(bot=FakeBot(), args=["TSLA1"])

    await TelegramDigestBot(make_settings()).why(update, context)

    assert "for example `/why TSLA`" in message.replies[0]


@pytest.mark.asyncio
async def test_why_sends_markdown_explanation(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeMomentumAnalysisService:
        def __init__(self, settings: Settings):
            self.settings = settings

        async def why(self, symbol: str) -> str:
            assert symbol == "TSLA"
            return "**Why TSLA?**\n**Read:** delivery chatter"

    monkeypatch.setattr("finage.telegram_bot.MomentumAnalysisService", FakeMomentumAnalysisService)
    message = FakeMessage()
    bot = FakeBot()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=999),
        effective_message=message,
    )
    context = SimpleNamespace(bot=bot, args=["tsla"])

    await TelegramDigestBot(make_settings()).why(update, context)

    assert message.replies == ["Analyzing why TSLA is moving socially..."]
    assert bot.messages[0]["chat_id"] == 999
    assert "<b>Why TSLA?</b>" in bot.messages[0]["text"]


@pytest.mark.asyncio
async def test_movers_sends_markdown_brief(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeMomentumAnalysisService:
        def __init__(self, settings: Settings):
            self.settings = settings

        async def movers(self) -> str:
            return "**Social Momentum Movers**\n- focused movers"

    monkeypatch.setattr("finage.telegram_bot.MomentumAnalysisService", FakeMomentumAnalysisService)
    message = FakeMessage()
    bot = FakeBot()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=999),
        effective_message=message,
    )
    context = SimpleNamespace(bot=bot, args=[])

    await TelegramDigestBot(make_settings()).movers(update, context)

    assert message.replies == ["Scanning social momentum movers..."]
    assert bot.messages[0]["chat_id"] == 999
    assert "<b>Social Momentum Movers</b>" in bot.messages[0]["text"]


@pytest.mark.asyncio
async def test_health_sends_markdown_report(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeMomentumAnalysisService:
        def __init__(self, settings: Settings):
            self.settings = settings

        async def health(self) -> str:
            return "**Finage Health**\nOverall: OK"

    monkeypatch.setattr("finage.telegram_bot.MomentumAnalysisService", FakeMomentumAnalysisService)
    message = FakeMessage()
    bot = FakeBot()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=999),
        effective_message=message,
    )
    context = SimpleNamespace(bot=bot, args=[])

    await TelegramDigestBot(make_settings()).health(update, context)

    assert message.replies == ["Checking Finage health..."]
    assert bot.messages[0]["chat_id"] == 999
    assert "<b>Finage Health</b>" in bot.messages[0]["text"]
