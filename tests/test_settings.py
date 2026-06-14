from pathlib import Path

from finage.settings import Settings


def test_settings_default_gemini_model_is_gemini_35_flash() -> None:
    settings = Settings(
        reddit_client_id="reddit-id",
        reddit_client_secret="reddit-secret",
        telegram_bot_token="telegram-token",
        gemini_api_key="gemini-key",
    )

    assert settings.gemini_model == "gemini-3.5-flash"


def test_from_env_defaults_gemini_model_to_gemini_35_flash(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("REDDIT_CLIENT_ID", "reddit-id")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "reddit-secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    settings = Settings.from_env(dotenv_path=tmp_path / "missing.env")

    assert settings.gemini_model == "gemini-3.5-flash"
