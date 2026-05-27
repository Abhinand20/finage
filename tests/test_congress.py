from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from finage.cli import build_parser
from finage.congress import (
    CongressAnalyzer,
    CongressCollector,
    amount_weight,
    normalize_congress_trade,
    parse_amount_range,
)
from finage.models import (
    CongressFetchMetadata,
    CongressSignal,
    CongressSnapshot,
    CongressTrade,
    PostEvidence,
    TickerEvidence,
    TrendingTicker,
)
from finage.prompting import render_congress_digest_prompt, render_congress_ticker_prompt
from finage.settings import Settings


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


def test_congress_models_validate_expected_fields() -> None:
    trade = CongressTrade(
        trade_id="abc123",
        source_hash="hash123",
        ticker="NVDA",
        asset_description="NVIDIA Corporation",
        representative="Jane Doe",
        chamber="House",
        transaction_type="Bought",
        raw_type="Purchase",
        amount="$100K-$250K",
        amount_min=100001,
        amount_max=250000,
        amount_midpoint=175000,
        raw_amount="$100,001 - $250,000",
        transaction_date=date(2026, 5, 1),
        disclosure_date=date(2026, 5, 20),
        disclosure_lag_days=19,
        first_seen_at=datetime(2026, 5, 21, tzinfo=UTC),
        last_seen_at=datetime(2026, 5, 21, tzinfo=UTC),
    )
    snapshot = CongressSnapshot(
        fetched_at=datetime(2026, 5, 21, tzinfo=UTC),
        total_trades=1,
        new_trades=1,
        corrected_trades=0,
        last_successful_fetch_at=datetime(2026, 5, 21, tzinfo=UTC),
        trades=[trade],
    )
    metadata = CongressFetchMetadata(
        fetched_at=datetime(2026, 5, 21, tzinfo=UTC),
        last_successful_fetch_at=datetime(2026, 5, 21, tzinfo=UTC),
        senate_pages=1,
        house_pages=1,
        total_records=2,
        new_trades=1,
        corrected_trades=0,
    )
    signal = CongressSignal(
        ticker="NVDA",
        asset_description="NVIDIA Corporation",
        total_score=7.5,
        net_buy_score=7.5,
        buy_count=1,
        sell_count=0,
        trade_count=1,
        new_trade_count=1,
        unique_politicians=1,
        total_bought_value_midpoint=175000,
        total_sold_value_midpoint=0,
        largest_amount="$100K-$250K",
        politicians=["Jane Doe"],
        latest_disclosure=date(2026, 5, 20),
        days_since_disclosure=1,
        avg_disclosure_lag_days=19.0,
        is_cluster=False,
        is_bicameral=False,
        convergence_score=10.5,
    )

    assert snapshot.trades == [trade]
    assert metadata.total_records == 2
    assert signal.total_bought_value_midpoint == 175000


def test_congress_settings_disable_feature_without_fmp_key(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    assert settings.fmp_api_key is None
    assert settings.congress_enabled is False
    assert settings.congress_lookback_days == 30
    assert settings.congress_bootstrap_days == 180
    assert settings.congress_max_pages_per_refresh == 4
    assert settings.congress_cache_ttl_hours == 12
    assert settings.congress_min_signal_score == 2.0


def test_congress_settings_enable_feature_with_fmp_key(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, fmp_api_key="fmp-key")

    assert settings.congress_enabled is True


def test_parse_amount_range_extracts_bounds_and_midpoint() -> None:
    parsed = parse_amount_range("$100,001 - $250,000")

    assert parsed.label == "$100K-$250K"
    assert parsed.amount_min == 100001
    assert parsed.amount_max == 250000
    assert parsed.amount_midpoint == 175000
    assert amount_weight(parsed.amount_midpoint) == 2.0


def test_normalize_congress_trade_maps_fmp_fields() -> None:
    now = datetime(2026, 5, 21, 12, tzinfo=UTC)
    trade = normalize_congress_trade(
        {
            "symbol": "nvda",
            "firstName": "Jane",
            "lastName": "Doe",
            "transactionDate": "2026-05-01",
            "disclosureDate": "2026-05-20",
            "type": "Purchase",
            "amount": "$100,001 - $250,000",
            "assetDescription": "NVIDIA Corporation",
        },
        chamber="House",
        seen_at=now,
    )

    assert trade.ticker == "NVDA"
    assert trade.representative == "Jane Doe"
    assert trade.chamber == "House"
    assert trade.transaction_type == "Bought"
    assert trade.amount == "$100K-$250K"
    assert trade.amount_midpoint == 175000
    assert trade.disclosure_lag_days == 19
    assert trade.trade_id
    assert trade.source_hash


def test_top_signals_rank_new_cluster_and_sales_lower() -> None:
    seen_at = datetime(2026, 5, 21, tzinfo=UTC)
    nvda_buy_1 = normalize_congress_trade(
        {
            "symbol": "NVDA",
            "firstName": "Jane",
            "lastName": "Doe",
            "transactionDate": "2026-05-01",
            "disclosureDate": "2026-05-20",
            "type": "Purchase",
            "amount": "$100,001 - $250,000",
            "assetDescription": "NVIDIA Corporation",
        },
        chamber="House",
        seen_at=seen_at,
    )
    nvda_buy_2 = normalize_congress_trade(
        {
            "symbol": "NVDA",
            "firstName": "John",
            "lastName": "Smith",
            "transactionDate": "2026-05-02",
            "disclosureDate": "2026-05-20",
            "type": "Purchase",
            "amount": "$15,001 - $50,000",
            "assetDescription": "NVIDIA Corporation",
        },
        chamber="Senate",
        seen_at=seen_at,
    )
    tsla_sale = normalize_congress_trade(
        {
            "symbol": "TSLA",
            "firstName": "Mary",
            "lastName": "Jones",
            "transactionDate": "2026-05-03",
            "disclosureDate": "2026-05-20",
            "type": "Sale",
            "amount": "$1,000,001 - $5,000,000",
            "assetDescription": "Tesla Inc.",
        },
        chamber="House",
        seen_at=seen_at,
    )

    signals = CongressAnalyzer(today=date(2026, 5, 21)).top_signals(
        [nvda_buy_1, nvda_buy_2, tsla_sale],
        new_trades=[nvda_buy_1],
        lookback_days=30,
        min_score=0,
    )

    assert signals[0].ticker == "NVDA"
    assert signals[0].buy_count == 2
    assert signals[0].new_trade_count == 1
    assert signals[0].is_cluster is True
    assert signals[0].is_bicameral is True
    assert signals[0].total_bought_value_midpoint == 207500
    assert signals[1].ticker == "TSLA"
    assert signals[1].sell_count == 1


def test_apply_convergence_scores_uses_social_rank_and_evidence() -> None:
    seen_at = datetime(2026, 5, 21, tzinfo=UTC)
    trade = normalize_congress_trade(
        {
            "symbol": "NVDA",
            "firstName": "Jane",
            "lastName": "Doe",
            "transactionDate": "2026-05-01",
            "disclosureDate": "2026-05-20",
            "type": "Purchase",
            "amount": "$100,001 - $250,000",
            "assetDescription": "NVIDIA Corporation",
        },
        chamber="House",
        seen_at=seen_at,
    )
    analyzer = CongressAnalyzer(today=date(2026, 5, 21))
    signals = analyzer.top_signals([trade], new_trades=[trade], lookback_days=30, min_score=0)

    enriched = analyzer.apply_convergence_scores(
        signals,
        reddit_tickers=[TrendingTicker(ticker="NVDA", rank=2, mentions=500, upvotes=1000)],
        evidence_by_ticker={
            "NVDA": TickerEvidence(
                ticker="NVDA",
                trending=TrendingTicker(ticker="NVDA", rank=2, mentions=500, upvotes=1000),
                posts=[
                    PostEvidence(
                        id="p1",
                        subreddit="stocks",
                        url="https://reddit.example/nvda",
                        title="NVDA thread",
                        score=1000,
                        num_comments=300,
                        mentioned_tickers=["NVDA"],
                    )
                ],
            )
        },
    )

    assert enriched[0].convergence_score is not None
    assert enriched[0].convergence_score > enriched[0].total_score
    assert "CONVERGENCE" in analyzer.format_signal_line(enriched[0], overlap=True)


def test_collector_persists_history_new_trades_metadata_and_ticker_files(tmp_path: Path) -> None:
    collector = CongressCollector(make_settings(tmp_path, fmp_api_key="fmp-key"))
    seen_at = datetime(2026, 5, 21, tzinfo=UTC)
    trade = normalize_congress_trade(
        {
            "symbol": "NVDA",
            "firstName": "Jane",
            "lastName": "Doe",
            "transactionDate": "2026-05-01",
            "disclosureDate": "2026-05-20",
            "type": "Purchase",
            "amount": "$100,001 - $250,000",
            "assetDescription": "NVIDIA Corporation",
        },
        chamber="House",
        seen_at=seen_at,
    )

    snapshot = collector.merge_and_persist([trade], fetched_at=seen_at, senate_pages=0, house_pages=1)

    assert snapshot.new_trades == 1
    assert (tmp_path / "congress" / "history.json").exists()
    assert (tmp_path / "congress" / "latest_fetch.json").exists()
    assert (tmp_path / "congress" / "new_trades.json").exists()
    assert (tmp_path / "congress" / "latest.json").exists()
    assert (tmp_path / "congress" / "by_ticker" / "NVDA.json").exists()
    assert collector.get_cached().trades[0].ticker == "NVDA"
    assert collector.read_new_trades()[0].trade_id == trade.trade_id


def test_collector_dedupes_existing_trade_and_tracks_corrections(tmp_path: Path) -> None:
    collector = CongressCollector(make_settings(tmp_path, fmp_api_key="fmp-key"))
    seen_at = datetime(2026, 5, 21, tzinfo=UTC)
    payload = {
        "symbol": "NVDA",
        "firstName": "Jane",
        "lastName": "Doe",
        "transactionDate": "2026-05-01",
        "disclosureDate": "2026-05-20",
        "type": "Purchase",
        "amount": "$100,001 - $250,000",
        "assetDescription": "NVIDIA Corporation",
    }
    first = normalize_congress_trade(payload, chamber="House", seen_at=seen_at)
    collector.merge_and_persist([first], fetched_at=seen_at, senate_pages=0, house_pages=1)

    second = first.model_copy(update={"source_hash": "changed-source-hash"})
    snapshot = collector.merge_and_persist(
        [second],
        fetched_at=datetime(2026, 5, 22, tzinfo=UTC),
        senate_pages=0,
        house_pages=1,
    )

    assert snapshot.total_trades == 1
    assert snapshot.new_trades == 0
    assert snapshot.corrected_trades == 1
    assert collector.get_cached().trades[0].source_hash == "changed-source-hash"
    assert collector.read_new_trades() == []


def test_trades_for_ticker_reads_ticker_file(tmp_path: Path) -> None:
    collector = CongressCollector(make_settings(tmp_path, fmp_api_key="fmp-key"))
    seen_at = datetime(2026, 5, 21, tzinfo=UTC)
    trade = normalize_congress_trade(
        {
            "symbol": "TSLA",
            "firstName": "Jane",
            "lastName": "Doe",
            "transactionDate": "2026-05-01",
            "disclosureDate": "2026-05-20",
            "type": "Purchase",
            "amount": "$15,001 - $50,000",
            "assetDescription": "Tesla Inc.",
        },
        chamber="Senate",
        seen_at=seen_at,
    )
    snapshot = collector.merge_and_persist([trade], fetched_at=seen_at, senate_pages=1, house_pages=0)

    assert collector.trades_for_ticker(snapshot, "tsla")[0].ticker == "TSLA"


class FakeFmpTransport:
    def __init__(self, pages: dict[tuple[str, int], list[dict]]):
        self.pages = pages
        self.calls: list[tuple[str, int]] = []
        self.limits: list[int] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        endpoint = "senate" if "senate-latest" in str(request.url) else "house"
        page = int(request.url.params.get("page", "0"))
        limit = int(request.url.params.get("limit", "0"))
        self.calls.append((endpoint, page))
        self.limits.append(limit)
        return httpx.Response(200, json=self.pages.get((endpoint, page), []), request=request)


def make_client_factory(transport: FakeFmpTransport):
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(transport), timeout=20)

    return factory


@pytest.mark.asyncio
async def test_fetch_paginates_until_daily_cutoff_and_persists(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        fmp_api_key="fmp-key",
        congress_max_pages_per_refresh=3,
    )
    collector = CongressCollector(
        settings,
        client_factory=make_client_factory(
            FakeFmpTransport(
                {
                    ("senate", 0): [
                        {
                            "symbol": "NVDA",
                            "firstName": "Jane",
                            "lastName": "Doe",
                            "transactionDate": "2026-05-01",
                            "disclosureDate": "2026-05-20",
                            "type": "Purchase",
                            "amount": "$100,001 - $250,000",
                            "assetDescription": "NVIDIA Corporation",
                        }
                    ],
                    ("senate", 1): [],
                    ("house", 0): [],
                }
            )
        ),
        now_provider=lambda: datetime(2026, 5, 21, tzinfo=UTC),
    )

    snapshot = await collector.fetch()

    assert snapshot.new_trades == 1
    assert collector.get_cached().trades[0].ticker == "NVDA"


@pytest.mark.asyncio
async def test_fetch_uses_25_record_pages_for_fmp_requests(tmp_path: Path) -> None:
    transport = FakeFmpTransport(
        {
            ("senate", 0): [
                {
                    "symbol": "NVDA",
                    "firstName": "Jane",
                    "lastName": "Doe",
                    "transactionDate": "2026-05-01",
                    "disclosureDate": "2026-05-20",
                    "type": "Purchase",
                    "amount": "$100,001 - $250,000",
                    "assetDescription": "NVIDIA Corporation",
                }
            ],
            ("senate", 1): [],
            ("house", 0): [],
        }
    )
    collector = CongressCollector(
        make_settings(tmp_path, fmp_api_key="fmp-key", congress_max_pages_per_refresh=3),
        client_factory=make_client_factory(transport),
        now_provider=lambda: datetime(2026, 5, 21, tzinfo=UTC),
    )

    await collector.fetch()

    assert transport.calls == [("senate", 0), ("senate", 1), ("house", 0)]
    assert transport.limits == [25, 25, 25]


@pytest.mark.asyncio
async def test_get_or_fetch_uses_fresh_cache_without_network(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, fmp_api_key="fmp-key", congress_cache_ttl_hours=12)
    first_collector = CongressCollector(settings, now_provider=lambda: datetime(2026, 5, 21, tzinfo=UTC))
    trade = normalize_congress_trade(
        {
            "symbol": "NVDA",
            "firstName": "Jane",
            "lastName": "Doe",
            "transactionDate": "2026-05-01",
            "disclosureDate": "2026-05-20",
            "type": "Purchase",
            "amount": "$100,001 - $250,000",
            "assetDescription": "NVIDIA Corporation",
        },
        chamber="House",
        seen_at=datetime(2026, 5, 21, tzinfo=UTC),
    )
    first_collector.merge_and_persist(
        [trade],
        fetched_at=datetime(2026, 5, 21, tzinfo=UTC),
        senate_pages=0,
        house_pages=1,
    )

    async def failing_factory() -> httpx.AsyncClient:
        raise AssertionError("network should not be called for fresh cache")

    collector = CongressCollector(
        settings,
        client_factory=failing_factory,
        now_provider=lambda: datetime(2026, 5, 21, 1, tzinfo=UTC),
    )

    snapshot = await collector.get_or_fetch()

    assert snapshot.total_trades == 1


@pytest.mark.asyncio
async def test_get_or_fetch_returns_stale_cache_when_fmp_fails(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, fmp_api_key="fmp-key", congress_cache_ttl_hours=1)
    first_collector = CongressCollector(settings, now_provider=lambda: datetime(2026, 5, 21, tzinfo=UTC))
    trade = normalize_congress_trade(
        {
            "symbol": "TSLA",
            "firstName": "Jane",
            "lastName": "Doe",
            "transactionDate": "2026-05-01",
            "disclosureDate": "2026-05-20",
            "type": "Purchase",
            "amount": "$15,001 - $50,000",
            "assetDescription": "Tesla Inc.",
        },
        chamber="House",
        seen_at=datetime(2026, 5, 21, tzinfo=UTC),
    )
    first_collector.merge_and_persist(
        [trade],
        fetched_at=datetime(2026, 5, 21, tzinfo=UTC),
        senate_pages=0,
        house_pages=1,
    )

    async def broken_transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request)

    collector = CongressCollector(
        settings,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(broken_transport), timeout=20),
        now_provider=lambda: datetime(2026, 5, 22, tzinfo=UTC),
    )

    snapshot = await collector.get_or_fetch()

    assert snapshot.trades[0].ticker == "TSLA"


def test_render_congress_digest_prompt_preserves_deterministic_labels() -> None:
    prompt = render_congress_digest_prompt(
        congress_section_markdown="- **NVDA** — NEW · 2 buys · Cluster · ⚡ CONVERGENCE",
        lookback_days=30,
        overlap_tickers=["NVDA"],
    )

    assert "CONGRESSIONAL TRADING DATA" in prompt
    assert "do not reorder tickers" in prompt
    assert "NEW" in prompt
    assert "NVDA" in prompt


def test_render_congress_ticker_prompt_includes_trade_history_and_social_context() -> None:
    prompt = render_congress_ticker_prompt(
        ticker="NVDA",
        asset_description="NVIDIA Corporation",
        trades_formatted="- 2026-05-20: Jane Doe bought $100K-$250K",
        reddit_rank="#2",
        ticker_limit=10,
    )

    assert "congressional trading disclosures for NVDA" in prompt
    assert "Jane Doe bought" in prompt
    assert "ApeWisdom Reddit rank: #2" in prompt
    assert "members of Congress" in prompt


def test_cli_supports_congress_refresh_command() -> None:
    args = build_parser().parse_args(["congress", "refresh"])

    assert args.command == "congress"
    assert args.congress_command == "refresh"
