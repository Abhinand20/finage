from pathlib import Path

import pytest

from finage.analysis import MomentumAnalysisService, format_live_brief, format_ticker_brief, normalize_ticker_symbol
from finage.models import CommentEvidence, PostEvidence, TickerEvidence, TrendingTicker, WsbSnapshot
from finage.settings import Settings


class FakeCollector:
    def __init__(self, snapshot: WsbSnapshot):
        self.snapshot = snapshot
        self.collected = False

    async def collect(self) -> WsbSnapshot:
        self.collected = True
        return self.snapshot


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        reddit_client_id="reddit-id",
        reddit_client_secret="reddit-secret",
        telegram_bot_token="telegram-token",
        telegram_allowed_ids=[123],
        telegram_default_chat_id=123,
        gemini_api_key="gemini-key",
        data_dir=tmp_path,
    )


def make_snapshot() -> WsbSnapshot:
    return WsbSnapshot(
        subreddit="wallstreetbets",
        subreddits=["stocks", "wallstreetbets", "options"],
        trending_tickers=[
            TrendingTicker(ticker="TSLA", rank=1, mentions=1200, upvotes=5000),
            TrendingTicker(ticker="NVDA", rank=2, mentions=700, upvotes=2500),
        ],
        ticker_evidence=[
            TickerEvidence(
                ticker="TSLA",
                trending=TrendingTicker(ticker="TSLA", rank=1, mentions=1200, upvotes=5000),
                posts=[
                    PostEvidence(
                        id="abc",
                        subreddit="stocks",
                        url="https://www.reddit.com/r/stocks/comments/abc",
                        title="TSLA delivery catalyst thread",
                        selftext="Delivery numbers and margin setup are driving the discussion.",
                        score=900,
                        num_comments=300,
                        mentioned_tickers=["TSLA"],
                        external_links=["https://example.com/tsla-catalyst"],
                        top_comments=[
                            CommentEvidence(
                                author="trader",
                                content="Deliveries are the setup.",
                                score=50,
                                mentioned_tickers=["TSLA"],
                            )
                        ],
                    ),
                    PostEvidence(
                        id="def",
                        subreddit="options",
                        url="https://www.reddit.com/r/options/comments/def",
                        title="TSLA calls discussion",
                        score=400,
                        num_comments=100,
                        mentioned_tickers=["TSLA"],
                    ),
                ],
            )
        ],
    )


def test_format_live_brief_includes_ranked_evidence_and_subreddit_breadth() -> None:
    brief = format_live_brief(make_snapshot())

    assert "**Live Social Momentum**" in brief
    assert "**TSLA** #1" in brief
    assert "1,200 mentions" in brief
    assert "evidence score 1,700" in brief
    assert "r/stocks x1, r/options x1" in brief
    assert "TSLA delivery catalyst thread" in brief
    assert "**NVDA** #2" in brief
    assert "No qualifying Reddit evidence found" in brief
    assert "not live price action" in brief


def test_format_live_brief_handles_empty_trending_list() -> None:
    brief = format_live_brief(WsbSnapshot(subreddit="wallstreetbets", subreddits=["stocks"]))

    assert "No trending tickers came back from ApeWisdom" in brief
    assert "r/stocks" in brief


def test_normalize_ticker_symbol_accepts_plain_and_cash_prefixed_symbols() -> None:
    assert normalize_ticker_symbol("tsla") == "TSLA"
    assert normalize_ticker_symbol("$nvda") == "NVDA"


def test_normalize_ticker_symbol_rejects_invalid_symbols() -> None:
    with pytest.raises(ValueError, match="Ticker must be 1-5 letters"):
        normalize_ticker_symbol("TSLA1")


def test_format_ticker_brief_includes_focused_evidence_card() -> None:
    brief = format_ticker_brief(make_snapshot(), "tsla")

    assert "**TSLA Social Momentum**" in brief
    assert "ApeWisdom rank: #1" in brief
    assert "Evidence score: 1,700" in brief
    assert "Subreddit breadth: r/stocks x1, r/options x1" in brief
    assert "Qualifying posts: 2" in brief
    assert "TSLA delivery catalyst thread" in brief
    assert "Summary: Delivery numbers" in brief
    assert "Comment (50): Deliveries are the setup." in brief
    assert "External links: https://example.com/tsla-catalyst" in brief


def test_format_ticker_brief_explains_missing_evidence_for_trending_ticker() -> None:
    brief = format_ticker_brief(make_snapshot(), "NVDA")

    assert "**NVDA Social Momentum**" in brief
    assert "is trending, but no qualifying Reddit posts" in brief
    assert "Current top tickers: TSLA #1, NVDA #2" in brief


def test_format_ticker_brief_explains_non_trending_ticker() -> None:
    brief = format_ticker_brief(make_snapshot(), "AMD")

    assert "**AMD Social Momentum**" in brief
    assert "not in the current ApeWisdom trend list" in brief
    assert "Finage did not collect targeted Reddit evidence" in brief


@pytest.mark.asyncio
async def test_live_service_collects_without_writing_snapshot_and_returns_brief(tmp_path: Path) -> None:
    snapshot = make_snapshot()
    collector = FakeCollector(snapshot)
    service = MomentumAnalysisService(make_settings(tmp_path), collector=collector)

    brief = await service.live()

    assert collector.collected
    assert "**TSLA** #1" in brief
    assert not (tmp_path / "latest_wsb_snapshot.json").exists()


@pytest.mark.asyncio
async def test_ticker_service_collects_without_writing_snapshot_and_returns_brief(tmp_path: Path) -> None:
    snapshot = make_snapshot()
    collector = FakeCollector(snapshot)
    service = MomentumAnalysisService(make_settings(tmp_path), collector=collector)

    brief = await service.ticker("$tsla")

    assert collector.collected
    assert "**TSLA Social Momentum**" in brief
    assert not (tmp_path / "latest_wsb_snapshot.json").exists()
