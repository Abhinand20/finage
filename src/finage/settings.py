from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

DEFAULT_STOCK_SUBREDDITS = [
    "stocks",
    "wallstreetbets",
    "options",
    "WallStreetbetsELITE",
    "Wallstreetbetsnew",
    "SPACs",
    "investing",
    "Daytrading",
    "pennystocks",
]


def _parse_int_list(raw: str | None) -> list[int]:
    if not raw:
        return []

    values: list[int] = []
    for item in raw.split(","):
        stripped = item.strip()
        if stripped:
            values.append(int(stripped))
    return values


def _optional_int(raw: str | None) -> int | None:
    if raw is None or raw.strip() == "":
        return None
    return int(raw)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


class Settings(BaseModel):
    reddit_client_id: str = Field(min_length=1)
    reddit_client_secret: str = Field(min_length=1)
    reddit_user_agent: str = "finage/0.1"

    telegram_bot_token: str = Field(min_length=1)
    telegram_allowed_ids: list[int] = Field(default_factory=list)
    telegram_default_chat_id: int | None = None

    llm_provider: Literal["gemini"] = "gemini"
    gemini_api_key: str = Field(min_length=1)
    gemini_model: str = "gemini-2.5-flash"

    wsb_subreddit: str = "wallstreetbets"
    wsb_subreddits: list[str] = Field(default_factory=lambda: list(DEFAULT_STOCK_SUBREDDITS))
    wsb_post_limit: int = 75
    wsb_ticker_limit: int = 10
    wsb_min_score: int = 100
    wsb_min_comments: int = 10
    wsb_top_comments: int = 5

    data_dir: Path = Path("data")
    digest_prompt_path: Path | None = None

    @classmethod
    def from_env(cls, *, dotenv_path: str | Path | None = None) -> "Settings":
        load_dotenv(dotenv_path=dotenv_path)

        return cls(
            reddit_client_id=os.environ["REDDIT_CLIENT_ID"],
            reddit_client_secret=os.environ["REDDIT_CLIENT_SECRET"],
            reddit_user_agent=os.getenv("REDDIT_USER_AGENT", "finage/0.1"),
            telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
            telegram_allowed_ids=_parse_int_list(os.getenv("TELEGRAM_ALLOWED_IDS")),
            telegram_default_chat_id=_optional_int(os.getenv("TELEGRAM_DEFAULT_CHAT_ID")),
            llm_provider=os.getenv("LLM_PROVIDER", "gemini"),
            gemini_api_key=os.environ["GEMINI_API_KEY"],
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            wsb_subreddit=os.getenv("WSB_SUBREDDIT", "wallstreetbets"),
            wsb_post_limit=_int_env("WSB_POST_LIMIT", 75),
            wsb_ticker_limit=_int_env("WSB_TICKER_LIMIT", 10),
            wsb_min_score=_int_env("WSB_MIN_SCORE", 100),
            wsb_min_comments=_int_env("WSB_MIN_COMMENTS", 10),
            wsb_top_comments=_int_env("WSB_TOP_COMMENTS", 5),
            data_dir=Path(os.getenv("FINAGE_DATA_DIR", "data")),
            digest_prompt_path=Path(os.environ["DIGEST_PROMPT_PATH"])
            if os.getenv("DIGEST_PROMPT_PATH")
            else None,
        )

    @property
    def telegram_allowed_id_set(self) -> set[int]:
        return set(self.telegram_allowed_ids)
