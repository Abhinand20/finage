import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from finage.settings import Settings
from finage.web_search import (
    ExaWebSearchProvider,
    WebSearchOptions,
    _contents_for_mode,
    _normalize_exa_result,
    create_web_search_provider,
)


def make_settings(**overrides) -> Settings:
    base = dict(
        reddit_client_id="reddit-id",
        reddit_client_secret="reddit-secret",
        telegram_bot_token="telegram-token",
        telegram_allowed_ids=[123],
        telegram_default_chat_id=123,
        gemini_api_key="gemini-key",
        data_dir=Path("data"),
    )
    base.update(overrides)
    return Settings(**base)


def test_web_search_is_disabled_without_exa_key() -> None:
    s = make_settings()
    assert s.exa_api_key is None
    assert s.web_search_provider is None
    assert s.digest_web_search_enabled is False
    assert s.web_search_num_results == 3
    assert s.web_search_content_mode == "highlights"
    assert s.web_search_timeout_seconds == 15


def test_web_search_defaults_to_exa_when_api_key_is_present() -> None:
    s = make_settings(exa_api_key="exa-key")
    assert s.web_search_provider == "exa"
    assert s.digest_web_search_enabled is True


def test_from_env_reads_optional_web_search_config(monkeypatch) -> None:
    monkeypatch.setenv("REDDIT_CLIENT_ID", "rid")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "rsec")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "ttok")
    monkeypatch.setenv("GEMINI_API_KEY", "gkey")
    monkeypatch.setenv("EXA_API_KEY", "ek")
    monkeypatch.setenv("WEB_SEARCH_NUM_RESULTS", "5")
    monkeypatch.setenv("WEB_SEARCH_CONTENT_MODE", "text")
    monkeypatch.setenv("WEB_SEARCH_TIMEOUT_SECONDS", "9")
    monkeypatch.setenv("DIGEST_WEB_SEARCH_ENABLED", "false")

    s = Settings.from_env()
    assert s.digest_web_search_enabled is False
    assert s.web_search_provider == "exa"
    assert s.web_search_num_results == 5
    assert s.web_search_content_mode == "text"
    assert s.web_search_timeout_seconds == 9


def test_create_web_search_provider_returns_exa_provider_when_digest_disabled() -> None:
    settings = make_settings(exa_api_key="exa-key", digest_web_search_enabled=False)

    assert isinstance(create_web_search_provider(settings), ExaWebSearchProvider)


def test_create_web_search_provider_returns_exa_provider() -> None:
    settings = make_settings(exa_api_key="exa-key", digest_web_search_enabled=True)

    provider = create_web_search_provider(settings)

    assert isinstance(provider, ExaWebSearchProvider)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("highlights", {"highlights": True}),
        ("text", {"text": {"maxCharacters": 2000}}),
        ("none", False),
    ],
)
def test_contents_for_mode(mode: str, expected: dict | bool) -> None:
    assert _contents_for_mode(mode) == expected


def test_normalize_exa_result_handles_optional_fields() -> None:
    raw = SimpleNamespace(
        title="Tesla news",
        url="https://example.com/tesla",
        published_date="2026-04-28",
        author=None,
        highlights=["earnings catalyst"],
        text="Long article text",
        score=0.91,
    )

    result = _normalize_exa_result(raw)

    assert result.title == "Tesla news"
    assert result.url == "https://example.com/tesla"
    assert result.published_date == "2026-04-28"
    assert result.author is None
    assert result.highlights == ["earnings catalyst"]
    assert result.text == "Long article text"
    assert result.score == 0.91


def test_normalize_exa_result_supports_camel_case_published_date() -> None:
    raw = SimpleNamespace(
        title="Tesla news",
        url="https://example.com/tesla",
        publishedDate="2026-04-28",
        highlights=[],
    )

    result = _normalize_exa_result(raw)

    assert result.published_date == "2026-04-28"


@pytest.mark.asyncio
async def test_exa_provider_search_normalizes_results() -> None:
    class FakeExaClient:
        async def search(self, query: str, **kwargs):
            assert query == "TSLA stock latest news"
            assert kwargs["num_results"] == 2
            assert kwargs["type"] == "auto"
            assert kwargs["contents"] == {"highlights": True}
            return SimpleNamespace(
                request_id="request-123",
                results=[
                    SimpleNamespace(
                        title="Tesla catalyst",
                        url="https://example.com/catalyst",
                        published_date="2026-04-28",
                        author="Reporter",
                        highlights=["Tesla catalyst highlight"],
                        text=None,
                        score=0.8,
                    )
                ],
            )

    provider = ExaWebSearchProvider(api_key="exa-key", client=FakeExaClient())

    response = await provider.search(
        "TSLA stock latest news",
        WebSearchOptions(num_results=2, content_mode="highlights"),
    )

    assert response.query == "TSLA stock latest news"
    assert response.provider == "exa"
    assert response.request_id == "request-123"
    assert response.results[0].title == "Tesla catalyst"


@pytest.mark.asyncio
async def test_exa_provider_search_enforces_timeout() -> None:
    class SlowExaClient:
        async def search(self, query: str, **kwargs):
            await asyncio.sleep(0.05)
            return SimpleNamespace(results=[])

    provider = ExaWebSearchProvider(
        api_key="exa-key",
        timeout_seconds=0.001,
        client=SlowExaClient(),
    )

    with pytest.raises(TimeoutError):
        await provider.search("TSLA stock latest news", WebSearchOptions())
