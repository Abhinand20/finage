from datetime import UTC, datetime
from pathlib import Path

import pytest

from finage.artifacts import ArtifactStore
from finage.analysis import (
    HealthCheck,
    MomentumAnalysisService,
    format_health_report,
    format_live_brief,
    format_movers_brief,
    format_ticker_brief,
    normalize_ticker_symbol,
)
from finage.congress import normalize_congress_trade
from finage.models import CongressSnapshot, CongressTrade, CommentEvidence, PostEvidence, TickerEvidence, TrendingTicker, WsbSnapshot
from finage.settings import Settings


class FakeCollector:
    def __init__(self, snapshot: WsbSnapshot):
        self.snapshot = snapshot
        self.collected = False

    async def collect(self) -> WsbSnapshot:
        self.collected = True
        return self.snapshot


class FakeHealthCollector(FakeCollector):
    def __init__(self, snapshot: WsbSnapshot, trending: list[TrendingTicker] | None = None):
        super().__init__(snapshot)
        self.trending = trending or []
        self.fetched_trending = False

    async def fetch_trending_tickers(self) -> list[TrendingTicker]:
        self.fetched_trending = True
        return self.trending


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "reddit_client_id": "reddit-id",
        "reddit_client_secret": "reddit-secret",
        "telegram_bot_token": "telegram-token",
        "telegram_allowed_ids": [123],
        "telegram_default_chat_id": 123,
        "gemini_api_key": "gemini-key",
        "data_dir": tmp_path,
    }
    values.update(overrides)
    return Settings(**values)


class FakeLlm:
    name = "fake"
    model = "fake-model"

    def __init__(self) -> None:
        self.last_prompt = ""
        self.calls = 0

    async def generate(self, prompt: str) -> str:
        self.calls += 1
        self.last_prompt = prompt
        return (
            "**TSLA read:** Delivery and options chatter are driving the discussion.\n"
            "**Sentiment:** Mixed with medium confidence.\n"
            "Not financial advice."
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


def make_previous_movers_snapshot() -> WsbSnapshot:
    return WsbSnapshot(
        subreddit="wallstreetbets",
        subreddits=["stocks", "wallstreetbets"],
        trending_tickers=[
            TrendingTicker(ticker="AMD", rank=1, mentions=900, upvotes=3000),
            TrendingTicker(ticker="TSLA", rank=2, mentions=1000, upvotes=4000),
        ],
        ticker_evidence=[
            TickerEvidence(
                ticker="TSLA",
                trending=TrendingTicker(ticker="TSLA", rank=2, mentions=1000, upvotes=4000),
                posts=[
                    PostEvidence(
                        id="old-tsla",
                        subreddit="stocks",
                        url="https://www.reddit.com/r/stocks/comments/old-tsla",
                        title="Old TSLA thread",
                        score=400,
                        num_comments=100,
                        mentioned_tickers=["TSLA"],
                    )
                ],
            ),
            TickerEvidence(
                ticker="AMD",
                trending=TrendingTicker(ticker="AMD", rank=1, mentions=900, upvotes=3000),
                posts=[
                    PostEvidence(
                        id="old-amd",
                        subreddit="stocks",
                        url="https://www.reddit.com/r/stocks/comments/old-amd",
                        title="Old AMD thread",
                        score=300,
                        num_comments=100,
                        mentioned_tickers=["AMD"],
                    )
                ],
            ),
        ],
    )


def make_current_movers_snapshot() -> WsbSnapshot:
    snapshot = make_snapshot()
    snapshot.trending_tickers.append(TrendingTicker(ticker="AMD", rank=3, mentions=850, upvotes=2500))
    snapshot.ticker_evidence.append(
        TickerEvidence(
            ticker="AMD",
            trending=TrendingTicker(ticker="AMD", rank=3, mentions=850, upvotes=2500),
            posts=[
                PostEvidence(
                    id="new-amd",
                    subreddit="stocks",
                    url="https://www.reddit.com/r/stocks/comments/new-amd",
                    title="AMD fading thread",
                    score=250,
                    num_comments=50,
                    mentioned_tickers=["AMD"],
                )
            ],
        )
    )
    return snapshot


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


def test_format_movers_brief_compares_current_scan_to_baseline() -> None:
    brief = format_movers_brief(make_current_movers_snapshot(), make_previous_movers_snapshot())

    assert "**Social Momentum Movers**" in brief
    assert "**NVDA** entered at #2" in brief
    assert "**TSLA** #2 -> #1 (up 1 spots)" in brief
    assert "**AMD** #1 -> #3 (down 2 spots)" in brief
    assert "**TSLA** +200 mentions to 1,200 total." in brief
    assert "**TSLA** +1,000 upvotes to 5,000 total." in brief
    assert "**TSLA** evidence score 1,700 (+1,200)" in brief
    assert "**TSLA** gained +1 subreddit sources" in brief


def test_format_movers_brief_explains_missing_baseline() -> None:
    brief = format_movers_brief(make_snapshot(), None)

    assert "No saved baseline snapshot found" in brief
    assert "Run `/digest` or `finage digest send` first" in brief
    assert "Current top tickers: TSLA #1, NVDA #2" in brief


def test_format_health_report_sets_overall_status_from_checks() -> None:
    report = format_health_report(
        [
            HealthCheck("OK", "A", "healthy"),
            HealthCheck("WARN", "B", "needs attention"),
        ]
    )

    assert "**Finage Health**" in report
    assert "Overall: WARN" in report
    assert "`OK` **A**: healthy" in report
    assert "`WARN` **B**: needs attention" in report


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


@pytest.mark.asyncio
async def test_why_service_calls_llm_with_focused_ticker_prompt(tmp_path: Path) -> None:
    snapshot = make_snapshot()
    collector = FakeCollector(snapshot)
    llm = FakeLlm()
    service = MomentumAnalysisService(make_settings(tmp_path), collector=collector, llm_provider=llm)

    brief = await service.why("tsla")

    assert collector.collected
    assert llm.calls == 1
    assert brief.startswith("**Why TSLA?**")
    assert "Delivery and options chatter" in brief
    assert "Explain why TSLA" in llm.last_prompt
    assert '"ticker": "TSLA"' in llm.last_prompt
    assert "TSLA delivery catalyst thread" in llm.last_prompt
    assert "NVDA" not in llm.last_prompt
    assert not (tmp_path / "latest_wsb_snapshot.json").exists()


@pytest.mark.asyncio
async def test_why_service_skips_llm_when_no_evidence_exists(tmp_path: Path) -> None:
    snapshot = make_snapshot()
    collector = FakeCollector(snapshot)
    llm = FakeLlm()
    service = MomentumAnalysisService(make_settings(tmp_path), collector=collector, llm_provider=llm)

    brief = await service.why("NVDA")

    assert collector.collected
    assert llm.calls == 0
    assert "**NVDA Social Momentum**" in brief
    assert "is trending, but no qualifying Reddit posts" in brief


@pytest.mark.asyncio
async def test_movers_service_compares_without_overwriting_latest_snapshot(tmp_path: Path) -> None:
    previous = make_previous_movers_snapshot()
    ArtifactStore(tmp_path).write_snapshot(previous)
    snapshot_path = tmp_path / "latest_wsb_snapshot.json"
    original_snapshot_json = snapshot_path.read_text(encoding="utf-8")
    collector = FakeCollector(make_current_movers_snapshot())
    service = MomentumAnalysisService(make_settings(tmp_path), collector=collector)

    brief = await service.movers()

    assert collector.collected
    assert "**Social Momentum Movers**" in brief
    assert "**NVDA** entered at #2" in brief
    assert snapshot_path.read_text(encoding="utf-8") == original_snapshot_json


@pytest.mark.asyncio
async def test_health_service_reports_ok_and_warn_states(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    store.write_snapshot(make_snapshot())
    collector = FakeHealthCollector(
        make_snapshot(),
        trending=[TrendingTicker(ticker="TSLA", rank=1, mentions=100, upvotes=200)],
    )
    service = MomentumAnalysisService(make_settings(tmp_path), collector=collector)

    report = await service.health()

    assert collector.fetched_trending
    assert "Overall: WARN" in report
    assert "`OK` **Telegram allowlist**" in report
    assert "`OK` **Latest snapshot**" in report
    assert "`WARN` **Latest digest**" in report
    assert "`OK` **ApeWisdom**: Fetched 1 trending tickers" in report


@pytest.mark.asyncio
async def test_health_service_reports_failures_without_network_call_requirements(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.telegram_allowed_ids = []
    settings.wsb_subreddits = []
    collector = FakeCollector(make_snapshot())
    service = MomentumAnalysisService(settings, collector=collector)

    report = await service.health()

    assert "Overall: FAIL" in report
    assert "`FAIL` **Telegram allowlist**" in report
    assert "`FAIL` **Collection settings**" in report
    assert "`WARN` **ApeWisdom**: Trending check skipped for injected collector" in report


class FakeCongressCollector:
    def __init__(self, trades: list[CongressTrade]):
        self.snapshot = CongressSnapshot(
            fetched_at=datetime(2026, 5, 21, tzinfo=UTC),
            total_trades=len(trades),
            new_trades=len(trades),
            corrected_trades=0,
            last_successful_fetch_at=datetime(2026, 5, 21, tzinfo=UTC),
            trades=trades,
        )
        self.new_trades = trades

    async def get_or_fetch(self):
        return self.snapshot

    def read_new_trades(self):
        return self.new_trades

    def trades_for_ticker(self, snapshot, ticker: str):
        return [trade for trade in snapshot.trades if trade.ticker == ticker.upper()]


def make_congress_trade(ticker: str = "TSLA") -> CongressTrade:
    return normalize_congress_trade(
        {
            "symbol": ticker,
            "firstName": "Jane",
            "lastName": "Doe",
            "transactionDate": "2026-05-01",
            "disclosureDate": "2026-05-20",
            "type": "Purchase",
            "amount": "$100,001 - $250,000",
            "assetDescription": f"{ticker} Inc.",
        },
        chamber="House",
        seen_at=datetime(2026, 5, 21, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_senate_service_calls_llm_with_congress_prompt(tmp_path: Path) -> None:
    llm = FakeLlm()
    service = MomentumAnalysisService(
        make_settings(tmp_path),
        collector=FakeHealthCollector(make_snapshot(), trending=make_snapshot().trending_tickers),
        llm_provider=llm,
        congress_collector=FakeCongressCollector([make_congress_trade("TSLA")]),
    )

    result = await service.senate("tsla")

    assert result.startswith("**Congressional Activity: TSLA**")
    assert llm.calls == 1
    assert "congressional trading disclosures for TSLA" in llm.last_prompt
    assert "Jane Doe" in llm.last_prompt
    assert "ApeWisdom Reddit rank: #1" in llm.last_prompt


@pytest.mark.asyncio
async def test_senate_service_calls_llm_when_no_trades_exist(tmp_path: Path) -> None:
    llm = FakeLlm()
    service = MomentumAnalysisService(
        make_settings(tmp_path),
        collector=FakeHealthCollector(make_snapshot(), trending=make_snapshot().trending_tickers),
        llm_provider=llm,
        congress_collector=FakeCongressCollector([]),
    )

    result = await service.senate("nvda")

    assert result.startswith("**Congressional Activity: NVDA**")
    assert "No congressional trades found in local history." in llm.last_prompt


@pytest.mark.asyncio
async def test_live_service_adds_congress_badge(tmp_path: Path) -> None:
    service = MomentumAnalysisService(
        make_settings(tmp_path, fmp_api_key="fmp-key"),
        collector=FakeCollector(make_snapshot()),
        congress_collector=FakeCongressCollector([make_congress_trade("TSLA")]),
    )

    result = await service.live()

    assert "**TSLA** #1 🏛️" in result
