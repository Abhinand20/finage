from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from finage.cli import build_parser
from finage.models import (
    CongressSignal,
    TrendingTicker,
    WhaleChange,
    WhaleFundSnapshot,
    WhaleHolding,
    WhaleSignal,
    WhaleSnapshot,
)
from finage.prompting import (
    render_whale_digest_prompt,
    render_whale_ticker_prompt,
    render_whales_digest_prompt,
)
from finage.settings import Settings
from finage.whale import (
    WHALE_WATCHLIST,
    WhaleAnalyzer,
    WhaleCollector,
    normalize_whale_change,
    normalize_whale_holding,
)


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


def test_whale_models_validate_expected_fields() -> None:
    holding = WhaleHolding(
        ticker="NVDA",
        cusip="67066G104",
        shares=100,
        value_usd=90_000,
        weight=0.15,
    )
    change = WhaleChange(
        status="NEW",
        ticker="NVDA",
        shares_delta=100,
        shares_delta_pct=None,
        value_delta_usd=90_000,
        prior_shares=0,
        prior_value_usd=0,
        current_shares=100,
        current_value_usd=90_000,
    )
    fund = WhaleFundSnapshot(
        slug="berkshire-hathaway",
        fund_name="Berkshire Hathaway",
        manager="Warren Buffett",
        cik="1067983",
        report_period=date(2026, 3, 31),
        filing_accession="0000950123-26-000001",
        total_holdings=1,
        total_value_usd=90_000,
        holdings=[holding],
        changes=[change],
        fetched_at=datetime(2026, 5, 26, tzinfo=UTC),
    )
    signal = WhaleSignal(
        ticker="NVDA",
        total_score=7.8,
        convergence_score=10.8,
        fund_count=1,
        new_count=1,
        increased_count=0,
        decreased_count=0,
        closed_count=0,
        total_value_usd=90_000,
        largest_position_fund="Berkshire Hathaway",
        largest_position_value_usd=90_000,
        funds=["Berkshire Hathaway"],
        has_social_overlap=True,
        has_congress_overlap=False,
        labels=["NEW POSITION", "WHALE + SOCIAL"],
    )
    snapshot = WhaleSnapshot(
        fetched_at=datetime(2026, 5, 26, tzinfo=UTC),
        funds=[fund],
        signals=[signal],
    )

    assert snapshot.funds[0].holdings == [holding]
    assert snapshot.signals[0].labels == ["NEW POSITION", "WHALE + SOCIAL"]


def test_whale_settings_disable_feature_without_edgar_identity(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    assert settings.edgar_identity is None
    assert settings.whale_enabled is False
    assert settings.whale_lookback_quarters == 2
    assert settings.whale_cache_ttl_hours == 24
    assert settings.whale_min_signal_score == 2.0


def test_whale_settings_enable_feature_with_edgar_identity(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, edgar_identity="finage@example.com")

    assert settings.whale_enabled is True


def test_whale_watchlist_has_ten_unique_funds() -> None:
    assert len(WHALE_WATCHLIST) == 10
    assert len({fund.slug for fund in WHALE_WATCHLIST}) == 10
    assert len({fund.cik for fund in WHALE_WATCHLIST}) == 10
    assert WHALE_WATCHLIST[0].slug == "berkshire-hathaway"
    assert WHALE_WATCHLIST[0].cik == "1067983"


def test_normalize_whale_holding_maps_dataframe_row() -> None:
    holding = normalize_whale_holding(
        {
            "Ticker": "nvda",
            "CUSIP": "67066G104",
            "Shares": "1,000",
            "Value": "2500",
        },
        total_value_usd=25_000_000,
    )

    assert holding.ticker == "NVDA"
    assert holding.cusip == "67066G104"
    assert holding.shares == 1_000
    assert holding.value_usd == 2_500_000
    assert holding.weight == 0.1


def test_normalize_whale_change_maps_comparison_row() -> None:
    change = normalize_whale_change(
        {
            "Ticker": "NVDA",
            "Status": "INCREASED",
            "Shares": 150,
            "Previous Shares": 100,
            "Value": 3000,
            "Previous Value": 2000,
        }
    )

    assert change.status == "INCREASED"
    assert change.shares_delta == 50
    assert change.shares_delta_pct == 50.0
    assert change.value_delta_usd == 1_000_000


class FakeFrame:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def to_dict(self, orient: str = "records") -> list[dict]:
        assert orient == "records"
        return self.rows


class FakeComparison:
    def __init__(self, rows: list[dict]):
        self.data = FakeFrame(rows)


class FakeThirteenF:
    management_company_name = "Berkshire Hathaway"
    report_period = date(2026, 3, 31)
    total_holdings = 1
    total_value = 2500
    accession_number = "0000950123-26-000001"

    def holdings_data(self):
        return FakeFrame([{"Ticker": "NVDA", "CUSIP": "67066G104", "Shares": 100, "Value": 2500}])

    def compare_holdings(self):
        return FakeComparison(
            [
                {
                    "Ticker": "NVDA",
                    "Status": "NEW",
                    "Shares": 100,
                    "Previous Shares": 0,
                    "Value": 2500,
                    "Previous Value": 0,
                }
            ]
        )


class FakeFilings:
    def __getitem__(self, index: int):
        assert index == 0
        return self

    def obj(self):
        return FakeThirteenF()


class FakeCompany:
    seen_ciks: list[str] = []

    def __init__(self, cik: str):
        self.cik = cik
        self.seen_ciks.append(cik)

    def get_filings(self, form: str):
        assert form == "13F-HR"
        return FakeFilings()


class FakeEdgar:
    Company = FakeCompany
    identities: list[str] = []

    @classmethod
    def set_identity(cls, identity: str) -> None:
        cls.identities.append(identity)


@pytest.mark.asyncio
async def test_whale_collector_refresh_persists_snapshot_and_ticker_files(tmp_path: Path) -> None:
    FakeCompany.seen_ciks = []
    FakeEdgar.identities = []
    settings = make_settings(tmp_path, edgar_identity="finage@example.com")
    collector = WhaleCollector(
        settings,
        edgar_module=FakeEdgar,
        now_provider=lambda: datetime(2026, 5, 26, tzinfo=UTC),
    )

    snapshot = await collector.refresh()

    assert FakeEdgar.identities == ["finage@example.com"]
    assert len(FakeCompany.seen_ciks) == 10
    assert snapshot.funds[0].slug == "berkshire-hathaway"
    assert snapshot.funds[0].holdings[0].ticker == "NVDA"
    assert (tmp_path / "whale" / "latest.json").exists()
    assert (tmp_path / "whale" / "signals.json").exists()
    assert (tmp_path / "whale" / "by_fund" / "berkshire-hathaway.json").exists()
    assert (tmp_path / "whale" / "by_ticker" / "NVDA.json").exists()


@pytest.mark.asyncio
async def test_whale_get_or_fetch_uses_fresh_cache_without_network(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, edgar_identity="finage@example.com")
    collector = WhaleCollector(
        settings,
        edgar_module=FakeEdgar,
        now_provider=lambda: datetime(2026, 5, 26, tzinfo=UTC),
    )
    await collector.refresh()

    class BrokenEdgar:
        @staticmethod
        def set_identity(identity: str) -> None:
            raise AssertionError("network should not be used for fresh cache")

    cached_collector = WhaleCollector(
        settings,
        edgar_module=BrokenEdgar,
        now_provider=lambda: datetime(2026, 5, 26, 1, tzinfo=UTC),
    )
    snapshot = await cached_collector.get_or_fetch()

    assert snapshot.funds


def make_whale_fund(
    slug: str,
    fund_name: str,
    change: WhaleChange,
    holding: WhaleHolding | None = None,
) -> WhaleFundSnapshot:
    return WhaleFundSnapshot(
        slug=slug,
        fund_name=fund_name,
        manager="Manager",
        cik="1",
        report_period=date(2026, 3, 31),
        total_holdings=1,
        total_value_usd=holding.value_usd if holding else change.current_value_usd,
        holdings=[holding] if holding else [],
        changes=[change],
        fetched_at=datetime(2026, 5, 26, tzinfo=UTC),
    )


def test_whale_analyzer_scores_new_and_clusters_above_reductions() -> None:
    nvda_new = WhaleChange(
        status="NEW",
        ticker="NVDA",
        shares_delta=100,
        shares_delta_pct=None,
        value_delta_usd=100_000_000,
        prior_shares=0,
        prior_value_usd=0,
        current_shares=100,
        current_value_usd=100_000_000,
    )
    nvda_add = WhaleChange(
        status="INCREASED",
        ticker="NVDA",
        shares_delta=50,
        shares_delta_pct=50.0,
        value_delta_usd=50_000_000,
        prior_shares=100,
        prior_value_usd=100_000_000,
        current_shares=150,
        current_value_usd=150_000_000,
    )
    tsla_trim = WhaleChange(
        status="DECREASED",
        ticker="TSLA",
        shares_delta=-25,
        shares_delta_pct=-25.0,
        value_delta_usd=-25_000_000,
        prior_shares=100,
        prior_value_usd=100_000_000,
        current_shares=75,
        current_value_usd=75_000_000,
    )

    signals = WhaleAnalyzer().top_signals(
        [
            make_whale_fund("berkshire-hathaway", "Berkshire Hathaway", nvda_new),
            make_whale_fund("pershing-square", "Pershing Square", nvda_add),
            make_whale_fund("appaloosa", "Appaloosa", tsla_trim),
        ],
        min_score=-10,
    )

    assert signals[0].ticker == "NVDA"
    assert signals[0].fund_count == 2
    assert "NEW POSITION" in signals[0].labels
    assert "BIG ADD" in signals[0].labels
    assert "MULTI-FUND CLUSTER" in signals[0].labels
    assert signals[0].total_score > signals[1].total_score


def test_whale_analyzer_applies_social_and_congress_convergence() -> None:
    signal = WhaleSignal(
        ticker="NVDA",
        total_score=6.0,
        fund_count=1,
        new_count=1,
        increased_count=0,
        decreased_count=0,
        closed_count=0,
        total_value_usd=100_000_000,
        largest_position_fund="Berkshire Hathaway",
        largest_position_value_usd=100_000_000,
        funds=["Berkshire Hathaway"],
        labels=["NEW POSITION"],
    )

    enriched = WhaleAnalyzer().apply_convergence(
        [signal],
        reddit_tickers=[TrendingTicker(ticker="NVDA", rank=2, mentions=100, upvotes=1000)],
        congress_signals=[
            CongressSignal(
                ticker="NVDA",
                asset_description="NVIDIA Corporation",
                total_score=3.0,
                net_buy_score=3.0,
                buy_count=1,
                sell_count=0,
                trade_count=1,
                new_trade_count=1,
                unique_politicians=1,
                total_bought_value_midpoint=100000,
                total_sold_value_midpoint=0,
                largest_amount="$100K-$250K",
                politicians=["Jane Doe"],
                latest_disclosure=date(2026, 5, 20),
                days_since_disclosure=1,
                avg_disclosure_lag_days=10.0,
                is_cluster=False,
                is_bicameral=False,
            )
        ],
    )

    assert enriched[0].convergence_score == 11.0
    assert enriched[0].has_social_overlap is True
    assert enriched[0].has_congress_overlap is True
    assert "WHALE + SOCIAL" in enriched[0].labels
    assert "WHALE + CONGRESS" in enriched[0].labels


def test_render_whale_ticker_prompt_includes_activity_and_caveat() -> None:
    prompt = render_whale_ticker_prompt(
        ticker="NVDA",
        asset_description="NVIDIA Corporation",
        watchlist_summary="10 curated 13F funds",
        fund_lines="- Berkshire Hathaway: NEW $100,000,000",
        labels=["NEW POSITION", "WHALE + SOCIAL"],
        reddit_rank="#2",
        congress_summary="No congressional overlap.",
    )

    assert "NVDA" in prompt
    assert "Berkshire Hathaway" in prompt
    assert "NEW POSITION, WHALE + SOCIAL" in prompt
    assert "quarterly and delayed" in prompt


def test_render_whales_digest_prompt_preserves_precomputed_order() -> None:
    prompt = render_whales_digest_prompt(signal_lines="- **NVDA** - NEW POSITION", top_n=5)

    assert "Top 5" in prompt
    assert "- **NVDA**" in prompt
    assert "do not reorder" in prompt.lower()


def test_render_whale_digest_prompt_builds_append_section() -> None:
    prompt = render_whale_digest_prompt(signal_lines="- **NVDA** - WHALE + SOCIAL")

    assert "--- WHALE 13F DATA ---" in prompt
    assert "## Whale Momentum" in prompt


def test_cli_parses_whale_refresh_command() -> None:
    args = build_parser().parse_args(["whale", "refresh"])

    assert args.command == "whale"
    assert args.whale_command == "refresh"
