import pytest

from finage.collector import WsbCollector, extract_external_links, extract_ticker_mentions
from finage.settings import Settings


def test_extract_ticker_mentions_prefers_candidate_tickers() -> None:
    candidates = ["TSLA", "NVDA", "A"]
    text = "WSB is watching $TSLA and nvda, but a normal article should not match ticker A."

    assert extract_ticker_mentions(text, candidates) == ["NVDA", "TSLA"]


def test_extract_ticker_mentions_allows_cash_prefixed_single_letter_tickers() -> None:
    assert extract_ticker_mentions("The post mentions $A calls.", ["A"]) == ["A"]


def test_extract_external_links_filters_reddit_media() -> None:
    links = extract_external_links(
        "See https://example.com/catalyst and https://reddit.com/r/wallstreetbets/comments/abc"
    )

    assert links == ["https://example.com/catalyst"]


def make_settings() -> Settings:
    return Settings(
        reddit_client_id="reddit-id",
        reddit_client_secret="reddit-secret",
        telegram_bot_token="telegram-token",
        telegram_allowed_ids=[123],
        telegram_default_chat_id=123,
        gemini_api_key="gemini-key",
    )


class FakeSubmissionWithoutComments:
    id = "abc"
    comments = None

    async def load(self) -> None:
        self.comments = None


@pytest.mark.asyncio
async def test_top_comments_handles_unloaded_none_comment_forest() -> None:
    collector = WsbCollector(make_settings())

    comments = await collector._top_comments(FakeSubmissionWithoutComments(), ["TSLA"])

    assert comments == []
