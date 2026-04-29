from pathlib import Path

from finage.settings import Settings


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
