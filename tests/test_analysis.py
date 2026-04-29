from pathlib import Path

import pytest

from finage.analysis import MomentumAnalysisService, format_live_brief
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
                        score=900,
                        num_comments=300,
                        mentioned_tickers=["TSLA"],
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


@pytest.mark.asyncio
async def test_live_service_collects_writes_snapshot_and_returns_brief(tmp_path: Path) -> None:
    snapshot = make_snapshot()
    collector = FakeCollector(snapshot)
    service = MomentumAnalysisService(make_settings(tmp_path), collector=collector)

    brief = await service.live()

    assert collector.collected
    assert "**TSLA** #1" in brief
    assert (tmp_path / "latest_wsb_snapshot.json").exists()
