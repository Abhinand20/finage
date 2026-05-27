# Whale Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add SEC 13F-powered whale tracking for a curated top-10 fund watchlist, with `/whale <TICKER>`, `/whales`, digest enrichment, and `finage whale refresh`.

**Architecture:** Create a focused `finage.whale` module that owns the watchlist, edgartools access, normalization, local JSON storage, deterministic scoring, and formatting helpers. Keep existing orchestration modules thin: `analysis.py` asks the whale module for ticker and digest-ready data, `digest.py` appends precomputed whale context to Gemini prompts, `telegram_bot.py` wires commands, and `cli.py` exposes refresh.

**Tech Stack:** Python 3.11, Pydantic v2, edgartools/SEC EDGAR, asyncio `to_thread` for blocking SEC calls, pytest, pytest-asyncio, Gemini through existing `LlmProvider`, Telegram through existing `python-telegram-bot`.

---

## File Structure

Create:
- `src/finage/whale.py` - top-10 watchlist, SEC/edgartools collector, normalization, storage, scoring, convergence, and format helpers.
- `src/finage/prompts/whale_ticker.md` - Gemini prompt for `/whale <TICKER>`.
- `src/finage/prompts/whales_digest.md` - Gemini prompt for `/whales`.
- `src/finage/prompts/whale_digest.md` - prompt fragment appended to the daily digest.
- `tests/test_whale.py` - watchlist, settings, models, collector, storage, analyzer, prompts, CLI parser tests.

Modify:
- `pyproject.toml` - add `edgartools`.
- `.env.example` - document `EDGAR_IDENTITY` and `WHALE_*` settings.
- `src/finage/models.py` - add whale Pydantic models.
- `src/finage/settings.py` - add whale settings and auto-disable without `EDGAR_IDENTITY`.
- `src/finage/prompting.py` - add whale prompt rendering helpers.
- `src/finage/analysis.py` - add `whale()` and `whales()` methods on `MomentumAnalysisService`.
- `src/finage/digest.py` - append whale section after congress section when enabled.
- `src/finage/telegram_bot.py` - register `/whale` and `/whales`.
- `src/finage/cli.py` - add `finage whale refresh`.
- `tests/test_analysis.py` - cover service-level whale commands.
- `tests/test_digest.py` - cover digest prompt enrichment.
- `tests/test_telegram_bot.py` - cover Telegram command routing and usage errors.

Do not modify:
- `brainstorm.md` unless the user explicitly asks; it may contain unrelated dirty notes.

---

### Task 1: Dependency, Models, And Settings

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/finage/models.py`
- Modify: `src/finage/settings.py`
- Modify: `.env.example`
- Test: `tests/test_whale.py`

- [ ] **Step 1: Write failing settings/model tests**

Create `tests/test_whale.py` with:

```python
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from finage.models import WhaleChange, WhaleFundSnapshot, WhaleHolding, WhaleSignal, WhaleSnapshot
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
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
uv run pytest tests/test_whale.py -v
```

Expected: FAIL with an import error for `WhaleHolding` or `WhaleSnapshot`.

- [ ] **Step 3: Add whale models**

Append these classes to `src/finage/models.py` after the congress models:

```python
class WhaleHolding(BaseModel):
    ticker: str
    cusip: str | None = None
    shares: int
    value_usd: int
    weight: float = 0.0


class WhaleChange(BaseModel):
    status: Literal["NEW", "INCREASED", "DECREASED", "CLOSED", "UNCHANGED"]
    ticker: str
    shares_delta: int
    shares_delta_pct: float | None = None
    value_delta_usd: int
    prior_shares: int
    prior_value_usd: int
    current_shares: int
    current_value_usd: int


class WhaleFundSnapshot(BaseModel):
    slug: str
    fund_name: str
    manager: str
    cik: str
    report_period: date
    filing_accession: str | None = None
    total_holdings: int = 0
    total_value_usd: int = 0
    holdings: list[WhaleHolding] = Field(default_factory=list)
    changes: list[WhaleChange] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WhaleSignal(BaseModel):
    ticker: str
    total_score: float
    convergence_score: float | None = None
    fund_count: int
    new_count: int
    increased_count: int
    decreased_count: int
    closed_count: int
    total_value_usd: int
    largest_position_fund: str
    largest_position_value_usd: int
    funds: list[str] = Field(default_factory=list)
    has_social_overlap: bool = False
    has_congress_overlap: bool = False
    labels: list[str] = Field(default_factory=list)


class WhaleSnapshot(BaseModel):
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    funds: list[WhaleFundSnapshot] = Field(default_factory=list)
    signals: list[WhaleSignal] = Field(default_factory=list)
```

- [ ] **Step 4: Add whale settings**

In `src/finage/settings.py`, add fields after the congress settings:

```python
    edgar_identity: str | None = None
    whale_lookback_quarters: int = 2
    whale_cache_ttl_hours: int = 24
    whale_min_signal_score: float = 2.0
    whale_enabled: bool = True
```

Update `model_post_init`:

```python
        if not self.edgar_identity:
            object.__setattr__(self, "whale_enabled", False)
```

Update `Settings.from_env()` kwargs:

```python
            "edgar_identity": _optional_str(os.getenv("EDGAR_IDENTITY")),
            "whale_lookback_quarters": _int_env("WHALE_LOOKBACK_QUARTERS", 2),
            "whale_cache_ttl_hours": _int_env("WHALE_CACHE_TTL_HOURS", 24),
            "whale_min_signal_score": float(os.getenv("WHALE_MIN_SIGNAL_SCORE", "2.0")),
```

- [ ] **Step 5: Add dependency and env documentation**

Add `edgartools` to `pyproject.toml` dependencies using the package manager:

```bash
uv add edgartools
```

Append to `.env.example`:

```dotenv
# SEC EDGAR / 13F whale tracking
EDGAR_IDENTITY=
WHALE_LOOKBACK_QUARTERS=2
WHALE_CACHE_TTL_HOURS=24
WHALE_MIN_SIGNAL_SCORE=2.0
```

- [ ] **Step 6: Run tests and verify pass**

Run:

```bash
uv run pytest tests/test_whale.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock .env.example src/finage/models.py src/finage/settings.py tests/test_whale.py
git commit -m "feat: add whale tracking models and settings"
```

---

### Task 2: Watchlist, Normalization, And Pure Helpers

**Files:**
- Create: `src/finage/whale.py`
- Modify: `tests/test_whale.py`

- [ ] **Step 1: Add failing helper tests**

Append to `tests/test_whale.py`:

```python
from finage.whale import (
    WHALE_WATCHLIST,
    WhaleFund,
    normalize_whale_change,
    normalize_whale_holding,
)


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
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
uv run pytest tests/test_whale.py::test_whale_watchlist_has_ten_unique_funds tests/test_whale.py::test_normalize_whale_holding_maps_dataframe_row tests/test_whale.py::test_normalize_whale_change_maps_comparison_row -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'finage.whale'`.

- [ ] **Step 3: Create `src/finage/whale.py` with watchlist and normalizers**

Create `src/finage/whale.py`:

```python
from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from finage.models import (
    CongressSignal,
    TickerEvidence,
    TrendingTicker,
    WhaleChange,
    WhaleFundSnapshot,
    WhaleHolding,
    WhaleSignal,
    WhaleSnapshot,
)
from finage.settings import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WhaleFund:
    slug: str
    fund_name: str
    manager: str
    cik: str
    quality_weight: float


WHALE_WATCHLIST: tuple[WhaleFund, ...] = (
    WhaleFund("berkshire-hathaway", "Berkshire Hathaway", "Warren Buffett", "1067983", 1.3),
    WhaleFund("pershing-square", "Pershing Square", "Bill Ackman", "1336528", 1.3),
    WhaleFund("scion-asset-management", "Scion Asset Management", "Michael Burry", "1649339", 1.3),
    WhaleFund("appaloosa", "Appaloosa", "David Tepper", "1656456", 1.1),
    WhaleFund("duquesne-family-office", "Duquesne Family Office", "Stanley Druckenmiller", "1536411", 1.1),
    WhaleFund("bridgewater-associates", "Bridgewater Associates", "Ray Dalio", "1350694", 1.1),
    WhaleFund("citadel-advisors", "Citadel Advisors", "Ken Griffin", "1423053", 1.1),
    WhaleFund("tiger-global", "Tiger Global", "Chase Coleman", "1167483", 1.0),
    WhaleFund("coatue-management", "Coatue Management", "Philippe Laffont", "1135730", 1.0),
    WhaleFund("soros-fund-management", "Soros Fund Management", "George Soros", "1029160", 1.0),
)


def _first_present(row: dict[str, Any], *names: str, default: Any = None) -> Any:
    lowered = {str(key).lower().replace("_", " ").strip(): value for key, value in row.items()}
    for name in names:
        key = name.lower().replace("_", " ").strip()
        if key in lowered:
            return lowered[key]
    return default


def _clean_int(value: Any) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return int(str(value).replace(",", "").replace("$", "").strip() or "0")


def _value_to_usd(value: Any) -> int:
    # 13F information-table values are conventionally reported in thousands of dollars.
    return _clean_int(value) * 1000


def normalize_whale_holding(row: dict[str, Any], *, total_value_usd: int) -> WhaleHolding:
    ticker = str(_first_present(row, "Ticker", "Symbol", "ticker", default="")).upper().strip()
    value_usd = _value_to_usd(_first_present(row, "Value", "value", "Value (x$1000)", default=0))
    return WhaleHolding(
        ticker=ticker,
        cusip=str(_first_present(row, "CUSIP", "cusip", default="") or "").strip() or None,
        shares=_clean_int(_first_present(row, "Shares", "sshPrnamt", "shares", default=0)),
        value_usd=value_usd,
        weight=round(value_usd / total_value_usd, 6) if total_value_usd else 0.0,
    )


def normalize_whale_change(row: dict[str, Any]) -> WhaleChange:
    status = str(_first_present(row, "Status", "status", default="UNCHANGED")).upper().strip()
    ticker = str(_first_present(row, "Ticker", "Symbol", "ticker", default="")).upper().strip()
    current_shares = _clean_int(_first_present(row, "Shares", "Current Shares", default=0))
    prior_shares = _clean_int(_first_present(row, "Previous Shares", "Prior Shares", default=0))
    current_value = _value_to_usd(_first_present(row, "Value", "Current Value", default=0))
    prior_value = _value_to_usd(_first_present(row, "Previous Value", "Prior Value", default=0))
    shares_delta = current_shares - prior_shares
    shares_delta_pct = None if prior_shares == 0 else round((shares_delta / prior_shares) * 100, 2)
    return WhaleChange(
        status=status,
        ticker=ticker,
        shares_delta=shares_delta,
        shares_delta_pct=shares_delta_pct,
        value_delta_usd=current_value - prior_value,
        prior_shares=prior_shares,
        prior_value_usd=prior_value,
        current_shares=current_shares,
        current_value_usd=current_value,
    )
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
uv run pytest tests/test_whale.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finage/whale.py tests/test_whale.py
git commit -m "feat: add whale watchlist and normalization"
```

---

### Task 3: Collector, Cache, And Storage

**Files:**
- Modify: `src/finage/whale.py`
- Modify: `tests/test_whale.py`

- [ ] **Step 1: Add fake edgartools objects and collector tests**

Append to `tests/test_whale.py`:

```python
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


async def test_whale_collector_refresh_persists_snapshot_and_ticker_files(tmp_path: Path) -> None:
    from finage.whale import WhaleCollector

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


async def test_whale_get_or_fetch_uses_fresh_cache_without_network(tmp_path: Path) -> None:
    from finage.whale import WhaleCollector

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
```

- [ ] **Step 2: Run collector tests to verify failure**

Run:

```bash
uv run pytest tests/test_whale.py::test_whale_collector_refresh_persists_snapshot_and_ticker_files tests/test_whale.py::test_whale_get_or_fetch_uses_fresh_cache_without_network -v
```

Expected: FAIL with `ImportError` for `WhaleCollector`.

- [ ] **Step 3: Add collector class**

Append to `src/finage/whale.py`:

```python
def _frame_records(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    if hasattr(frame, "to_dict"):
        return list(frame.to_dict("records"))
    return list(frame)


def _get_attr(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default)


class WhaleCollector:
    def __init__(
        self,
        settings: Settings,
        *,
        edgar_module: Any | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ):
        self.settings = settings
        self.base_dir = settings.data_dir / "whale"
        self.by_fund_dir = self.base_dir / "by_fund"
        self.by_ticker_dir = self.base_dir / "by_ticker"
        self.edgar_module = edgar_module
        self.now_provider = now_provider or (lambda: datetime.now(UTC))

    @property
    def latest_path(self) -> Path:
        return self.base_dir / "latest.json"

    @property
    def signals_path(self) -> Path:
        return self.base_dir / "signals.json"

    def _edgar(self) -> Any:
        if self.edgar_module is not None:
            return self.edgar_module
        import edgar

        return edgar

    def _write_json(self, path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    def get_cached(self) -> WhaleSnapshot | None:
        if not self.latest_path.exists():
            return None
        return WhaleSnapshot.model_validate_json(self.latest_path.read_text(encoding="utf-8"))

    def _is_cache_fresh(self, snapshot: WhaleSnapshot) -> bool:
        age_hours = (self.now_provider() - snapshot.fetched_at).total_seconds() / 3600
        return age_hours < self.settings.whale_cache_ttl_hours

    async def get_or_fetch(self) -> WhaleSnapshot:
        cached = self.get_cached()
        if cached is not None and self._is_cache_fresh(cached):
            return cached
        try:
            return await self.refresh()
        except Exception:
            logger.exception("Whale refresh failed")
            if cached is not None:
                return cached
            raise

    async def refresh(self) -> WhaleSnapshot:
        if not self.settings.edgar_identity:
            raise RuntimeError("EDGAR_IDENTITY is required for whale tracking")
        edgar = self._edgar()
        edgar.set_identity(self.settings.edgar_identity)
        fetched_at = self.now_provider()
        funds: list[WhaleFundSnapshot] = []
        for fund in WHALE_WATCHLIST:
            try:
                snapshot = await self._fetch_fund(edgar, fund, fetched_at=fetched_at)
            except Exception:
                logger.exception("Failed to fetch 13F for fund=%s cik=%s", fund.slug, fund.cik)
                continue
            funds.append(snapshot)

        analyzer = WhaleAnalyzer()
        signals = analyzer.top_signals(funds, min_score=self.settings.whale_min_signal_score)
        snapshot = WhaleSnapshot(fetched_at=fetched_at, funds=funds, signals=signals)
        self.persist(snapshot)
        return snapshot

    async def _fetch_fund(self, edgar: Any, fund: WhaleFund, *, fetched_at: datetime) -> WhaleFundSnapshot:
        import asyncio

        def load() -> Any:
            return edgar.Company(fund.cik).get_filings(form="13F-HR")[0].obj()

        filing = await asyncio.to_thread(load)
        total_value_usd = _value_to_usd(_get_attr(filing, "total_value", 0))
        holdings = [
            normalize_whale_holding(row, total_value_usd=total_value_usd)
            for row in _frame_records(filing.holdings_data())
            if _first_present(row, "Ticker", "Symbol", "ticker", default="")
        ]
        comparison = filing.compare_holdings()
        changes = [
            normalize_whale_change(row)
            for row in _frame_records(comparison.data)
            if _first_present(row, "Ticker", "Symbol", "ticker", default="")
        ]
        return WhaleFundSnapshot(
            slug=fund.slug,
            fund_name=fund.fund_name,
            manager=fund.manager,
            cik=fund.cik,
            report_period=_get_attr(filing, "report_period"),
            filing_accession=_get_attr(filing, "accession_number", None),
            total_holdings=_get_attr(filing, "total_holdings", len(holdings)),
            total_value_usd=total_value_usd,
            holdings=holdings,
            changes=changes,
            fetched_at=fetched_at,
        )

    def persist(self, snapshot: WhaleSnapshot) -> None:
        self._write_json(self.latest_path, snapshot.model_dump_json(indent=2))
        self._write_json(self.signals_path, json.dumps([s.model_dump(mode="json") for s in snapshot.signals], indent=2))
        for fund in snapshot.funds:
            self._write_json(self.by_fund_dir / f"{fund.slug}.json", fund.model_dump_json(indent=2))
        self._write_ticker_files(snapshot)

    def _write_ticker_files(self, snapshot: WhaleSnapshot) -> None:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for fund in snapshot.funds:
            holdings_by_ticker = {holding.ticker: holding for holding in fund.holdings}
            changes_by_ticker = {change.ticker: change for change in fund.changes}
            for ticker in sorted(set(holdings_by_ticker) | set(changes_by_ticker)):
                grouped[ticker].append(
                    {
                        "fund": fund.fund_name,
                        "manager": fund.manager,
                        "slug": fund.slug,
                        "report_period": fund.report_period.isoformat(),
                        "holding": holdings_by_ticker.get(ticker).model_dump(mode="json")
                        if ticker in holdings_by_ticker
                        else None,
                        "change": changes_by_ticker.get(ticker).model_dump(mode="json")
                        if ticker in changes_by_ticker
                        else None,
                    }
                )

        self.by_ticker_dir.mkdir(parents=True, exist_ok=True)
        for ticker, rows in grouped.items():
            self._write_json(self.by_ticker_dir / f"{ticker}.json", json.dumps(rows, indent=2, default=str))

    def activities_for_ticker(self, ticker: str) -> list[dict[str, Any]]:
        path = self.by_ticker_dir / f"{ticker.upper().strip()}.json"
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))
```

This references `WhaleAnalyzer`, which is implemented in Task 4.

- [ ] **Step 4: Add a temporary minimal analyzer shim**

Add this temporary class at the end of `src/finage/whale.py`; Task 4 replaces it with the full implementation:

```python
class WhaleAnalyzer:
    def top_signals(self, funds: list[WhaleFundSnapshot], *, min_score: float) -> list[WhaleSignal]:
        return []
```

- [ ] **Step 5: Run collector tests and verify pass**

Run:

```bash
uv run pytest tests/test_whale.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/finage/whale.py tests/test_whale.py
git commit -m "feat: collect and cache whale filings"
```

---

### Task 4: Scoring, Labels, And Convergence

**Files:**
- Modify: `src/finage/whale.py`
- Modify: `tests/test_whale.py`

- [ ] **Step 1: Add failing analyzer tests**

Append to `tests/test_whale.py`:

```python
from finage.models import CongressSignal, PostEvidence, TickerEvidence, TrendingTicker
from finage.whale import WhaleAnalyzer


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
```

- [ ] **Step 2: Run analyzer tests to verify failure**

Run:

```bash
uv run pytest tests/test_whale.py::test_whale_analyzer_scores_new_and_clusters_above_reductions tests/test_whale.py::test_whale_analyzer_applies_social_and_congress_convergence -v
```

Expected: FAIL because the temporary analyzer returns no signals and has no `apply_convergence`.

- [ ] **Step 3: Replace temporary analyzer with full implementation**

Replace the temporary `WhaleAnalyzer` in `src/finage/whale.py` with:

```python
def _action_weight(status: str) -> float:
    return {
        "NEW": 3.0,
        "INCREASED": 2.0,
        "DECREASED": -1.0,
        "CLOSED": -2.0,
        "UNCHANGED": 0.0,
    }.get(status, 0.0)


def _magnitude_weight(change: WhaleChange) -> float:
    if change.status == "NEW":
        return 2.0
    pct = abs(change.shares_delta_pct or 0.0)
    if pct >= 100:
        return 2.0
    if pct >= 25:
        return 1.5
    if pct >= 10:
        return 1.0
    return 0.5


def _cluster_bonus(direction_count: int) -> float:
    if direction_count >= 3:
        return 2.0
    if direction_count == 2:
        return 1.5
    return 1.0


class WhaleAnalyzer:
    def _quality_weight(self, fund_slug: str) -> float:
        by_slug = {fund.slug: fund.quality_weight for fund in WHALE_WATCHLIST}
        return by_slug.get(fund_slug, 1.0)

    def _change_score(self, fund: WhaleFundSnapshot, change: WhaleChange) -> float:
        return _action_weight(change.status) * _magnitude_weight(change) * self._quality_weight(fund.slug)

    def top_signals(self, funds: list[WhaleFundSnapshot], *, min_score: float) -> list[WhaleSignal]:
        grouped: dict[str, list[tuple[WhaleFundSnapshot, WhaleChange]]] = defaultdict(list)
        holdings_by_ticker: dict[str, list[tuple[WhaleFundSnapshot, WhaleHolding]]] = defaultdict(list)
        for fund in funds:
            for holding in fund.holdings:
                holdings_by_ticker[holding.ticker].append((fund, holding))
            for change in fund.changes:
                if change.status != "UNCHANGED":
                    grouped[change.ticker].append((fund, change))

        signals: list[WhaleSignal] = []
        for ticker, rows in grouped.items():
            positive_count = sum(1 for _, change in rows if change.status in {"NEW", "INCREASED"})
            raw_score = sum(self._change_score(fund, change) for fund, change in rows)
            total_score = round(raw_score * _cluster_bonus(positive_count), 2)
            if total_score < min_score:
                continue

            holding_rows = holdings_by_ticker.get(ticker, [])
            largest = max(
                holding_rows,
                key=lambda item: item[1].value_usd,
                default=(rows[0][0], WhaleHolding(ticker=ticker, shares=0, value_usd=0)),
            )
            labels: list[str] = []
            if any(change.status == "NEW" for _, change in rows):
                labels.append("NEW POSITION")
            if any(change.status == "INCREASED" and (change.shares_delta_pct or 0) >= 25 for _, change in rows):
                labels.append("BIG ADD")
            if positive_count >= 2:
                labels.append("MULTI-FUND CLUSTER")
            if any(change.status == "DECREASED" for _, change in rows):
                labels.append("REDUCTION")
            if any(change.status == "CLOSED" for _, change in rows):
                labels.append("FULL EXIT")

            signals.append(
                WhaleSignal(
                    ticker=ticker,
                    total_score=total_score,
                    fund_count=len({fund.slug for fund, _ in rows}),
                    new_count=sum(1 for _, change in rows if change.status == "NEW"),
                    increased_count=sum(1 for _, change in rows if change.status == "INCREASED"),
                    decreased_count=sum(1 for _, change in rows if change.status == "DECREASED"),
                    closed_count=sum(1 for _, change in rows if change.status == "CLOSED"),
                    total_value_usd=sum(holding.value_usd for _, holding in holding_rows),
                    largest_position_fund=largest[0].fund_name,
                    largest_position_value_usd=largest[1].value_usd,
                    funds=sorted({fund.fund_name for fund, _ in rows}),
                    labels=labels,
                )
            )

        return sorted(signals, key=lambda item: item.total_score, reverse=True)

    def apply_convergence(
        self,
        signals: list[WhaleSignal],
        *,
        reddit_tickers: list[TrendingTicker],
        congress_signals: list[CongressSignal] | None,
    ) -> list[WhaleSignal]:
        rank_by_ticker = {item.ticker: item.rank for item in reddit_tickers}
        congress_tickers = {item.ticker for item in congress_signals or []}
        enriched: list[WhaleSignal] = []
        for signal in signals:
            rank = rank_by_ticker.get(signal.ticker)
            social_bonus = 0.0
            labels = list(signal.labels)
            if rank is not None:
                social_bonus = 3.0 if rank <= 3 else 2.0 if rank <= 10 else 1.0 if rank <= 25 else 0.0
                if "WHALE + SOCIAL" not in labels:
                    labels.append("WHALE + SOCIAL")
            congress_bonus = 2.0 if signal.ticker in congress_tickers else 0.0
            if congress_bonus and "WHALE + CONGRESS" not in labels:
                labels.append("WHALE + CONGRESS")
            enriched.append(
                signal.model_copy(
                    update={
                        "convergence_score": round(signal.total_score + social_bonus + congress_bonus, 2),
                        "has_social_overlap": rank is not None,
                        "has_congress_overlap": signal.ticker in congress_tickers,
                        "labels": labels,
                    }
                )
            )

        return sorted(enriched, key=lambda item: item.convergence_score or item.total_score, reverse=True)

    def format_signal_line(self, signal: WhaleSignal) -> str:
        label_text = " | ".join(signal.labels) if signal.labels else "WHALE ACTIVITY"
        return (
            f"- **{signal.ticker}** - {label_text} | {signal.fund_count} funds | "
            f"${signal.total_value_usd:,} current exposure | largest: "
            f"{signal.largest_position_fund} ${signal.largest_position_value_usd:,}"
        )
```

- [ ] **Step 4: Run analyzer tests and verify pass**

Run:

```bash
uv run pytest tests/test_whale.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finage/whale.py tests/test_whale.py
git commit -m "feat: score whale momentum signals"
```

---

### Task 5: Prompt Templates And Renderers

**Files:**
- Create: `src/finage/prompts/whale_ticker.md`
- Create: `src/finage/prompts/whales_digest.md`
- Create: `src/finage/prompts/whale_digest.md`
- Modify: `src/finage/prompting.py`
- Modify: `tests/test_whale.py`

- [ ] **Step 1: Add failing prompt-rendering tests**

Append to `tests/test_whale.py`:

```python
from finage.prompting import (
    render_whale_digest_prompt,
    render_whale_ticker_prompt,
    render_whales_digest_prompt,
)


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

    assert "top 5" in prompt
    assert "- **NVDA**" in prompt
    assert "do not reorder" in prompt.lower()


def test_render_whale_digest_prompt_builds_append_section() -> None:
    prompt = render_whale_digest_prompt(signal_lines="- **NVDA** - WHALE + SOCIAL")

    assert "--- WHALE 13F DATA ---" in prompt
    assert "## Whale Momentum" in prompt
```

- [ ] **Step 2: Run prompt tests to verify failure**

Run:

```bash
uv run pytest tests/test_whale.py::test_render_whale_ticker_prompt_includes_activity_and_caveat tests/test_whale.py::test_render_whales_digest_prompt_preserves_precomputed_order tests/test_whale.py::test_render_whale_digest_prompt_builds_append_section -v
```

Expected: FAIL with import error for whale prompt renderers.

- [ ] **Step 3: Create prompt templates**

Create `src/finage/prompts/whale_ticker.md`:

```markdown
You are analyzing institutional Form 13F holdings for {ticker} ({asset_description}).

Watchlist funds covered:
{watchlist_summary}

Whale activity for {ticker} (precomputed by code, sorted by latest filing):
{fund_lines}

Signal labels: {labels}

Current social and congressional context:
- ApeWisdom Reddit rank: {reddit_rank}
- Congressional activity: {congress_summary}

Provide a concise Telegram-ready analysis covering:
1. Overall whale pattern: who added, who reduced, who exited.
2. Conviction signal: NEW, BIG ADD, MULTI-FUND CLUSTER, REDUCTION, or FULL EXIT dominant?
3. Convergence with social/congress data.
4. Caveats: 13F is quarterly and delayed up to 45 days.

Do not invent funds. Do not present this as financial advice. Keep the response under 400 words.
```

Create `src/finage/prompts/whales_digest.md`:

```markdown
You are writing a Telegram-friendly whale momentum brief from precomputed SEC 13F signals.

Top {top_n} whale signals, already sorted by code. Preserve this order and do not reorder:
{signal_lines}

Write a concise "## Whale Momentum" section.
Preserve all labels exactly.
Mention that 13F filings are quarterly and delayed up to 45 days.
Do not invent funds, holdings, or tickers.
Do not present this as financial advice.
```

Create `src/finage/prompts/whale_digest.md`:

```markdown
--- WHALE 13F DATA ---
Latest available Form 13F-HR filings for the curated top-10 fund watchlist. Signals and labels are precomputed by code; do not reorder tickers or invent additional funds.

{signal_lines}

Instructions: After congressional activity, add a "## Whale Momentum" section.
Include NEW POSITION, BIG ADD, MULTI-FUND CLUSTER, REDUCTION, FULL EXIT, WHALE + SOCIAL, and WHALE + CONGRESS labels when present.
Mention that 13F filings are quarterly and delayed up to 45 days.
If no whale signals exist above the threshold, write a single line:
"No notable whale 13F momentum this period."
--- END WHALE 13F DATA ---
```

- [ ] **Step 4: Add renderer functions**

Append to `src/finage/prompting.py` after congress renderers:

```python
def render_whale_ticker_prompt(
    *,
    ticker: str,
    asset_description: str,
    watchlist_summary: str,
    fund_lines: str,
    labels: list[str],
    reddit_rank: str,
    congress_summary: str,
) -> str:
    template = resources.files("finage.prompts").joinpath("whale_ticker.md").read_text(encoding="utf-8")
    return template.format(
        ticker=ticker,
        asset_description=asset_description,
        watchlist_summary=watchlist_summary,
        fund_lines=fund_lines,
        labels=", ".join(labels) if labels else "None",
        reddit_rank=reddit_rank,
        congress_summary=congress_summary,
    )


def render_whales_digest_prompt(*, signal_lines: str, top_n: int) -> str:
    template = resources.files("finage.prompts").joinpath("whales_digest.md").read_text(encoding="utf-8")
    return template.format(signal_lines=signal_lines, top_n=top_n)


def render_whale_digest_prompt(*, signal_lines: str) -> str:
    template = resources.files("finage.prompts").joinpath("whale_digest.md").read_text(encoding="utf-8")
    return template.format(signal_lines=signal_lines)
```

- [ ] **Step 5: Run tests and verify pass**

Run:

```bash
uv run pytest tests/test_whale.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/finage/prompts/whale_ticker.md src/finage/prompts/whales_digest.md src/finage/prompts/whale_digest.md src/finage/prompting.py tests/test_whale.py
git commit -m "feat: add whale analysis prompts"
```

---

### Task 6: Analysis Service Methods

**Files:**
- Modify: `src/finage/analysis.py`
- Modify: `tests/test_analysis.py`

- [ ] **Step 1: Add fake whale collector and service tests**

Append to `tests/test_analysis.py`:

```python
from finage.models import WhaleChange, WhaleFundSnapshot, WhaleHolding, WhaleSignal, WhaleSnapshot


class FakeWhaleCollector:
    def __init__(self, snapshot: WhaleSnapshot, activities: list[dict] | None = None):
        self.snapshot = snapshot
        self.activities = activities or []

    async def get_or_fetch(self) -> WhaleSnapshot:
        return self.snapshot

    def activities_for_ticker(self, ticker: str) -> list[dict]:
        return self.activities if ticker.upper() == "NVDA" else []


def make_whale_snapshot() -> WhaleSnapshot:
    return WhaleSnapshot(
        fetched_at=datetime(2026, 5, 26, tzinfo=UTC),
        funds=[
            WhaleFundSnapshot(
                slug="berkshire-hathaway",
                fund_name="Berkshire Hathaway",
                manager="Warren Buffett",
                cik="1067983",
                report_period=date(2026, 3, 31),
                total_holdings=1,
                total_value_usd=100_000_000,
                holdings=[WhaleHolding(ticker="NVDA", shares=100, value_usd=100_000_000)],
                changes=[
                    WhaleChange(
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
                ],
                fetched_at=datetime(2026, 5, 26, tzinfo=UTC),
            )
        ],
        signals=[
            WhaleSignal(
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
        ],
    )


async def test_whale_service_calls_llm_with_ticker_prompt(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, edgar_identity="finage@example.com")
    llm = FakeLlmProvider("whale read")
    service = MomentumAnalysisService(
        settings,
        collector=FakeCollector(WsbSnapshot(subreddit="wallstreetbets", trending_tickers=[])),
        llm_provider=llm,
        whale_collector=FakeWhaleCollector(
            make_whale_snapshot(),
            activities=[
                {
                    "fund": "Berkshire Hathaway",
                    "manager": "Warren Buffett",
                    "report_period": "2026-03-31",
                    "holding": {"ticker": "NVDA", "value_usd": 100_000_000, "shares": 100},
                    "change": {"status": "NEW", "value_delta_usd": 100_000_000},
                }
            ],
        ),
    )

    response = await service.whale("NVDA")

    assert response.startswith("**Whale Activity: NVDA**")
    assert "whale read" in response
    assert "Berkshire Hathaway" in llm.prompts[0]


async def test_whales_service_calls_llm_with_top_signals(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, edgar_identity="finage@example.com")
    llm = FakeLlmProvider("whales digest")
    service = MomentumAnalysisService(
        settings,
        collector=FakeCollector(WsbSnapshot(subreddit="wallstreetbets", trending_tickers=[])),
        llm_provider=llm,
        whale_collector=FakeWhaleCollector(make_whale_snapshot()),
    )

    response = await service.whales()

    assert response.startswith("**Whale Momentum**")
    assert "whales digest" in response
    assert "NVDA" in llm.prompts[0]
```

- [ ] **Step 2: Run service tests to verify failure**

Run:

```bash
uv run pytest tests/test_analysis.py::test_whale_service_calls_llm_with_ticker_prompt tests/test_analysis.py::test_whales_service_calls_llm_with_top_signals -v
```

Expected: FAIL because `MomentumAnalysisService.__init__` does not accept `whale_collector`.

- [ ] **Step 3: Modify `analysis.py` imports and constructor**

Add imports:

```python
from finage.whale import WHALE_WATCHLIST, WhaleAnalyzer, WhaleCollector
from finage.models import WhaleSignal
from finage.prompting import render_whale_ticker_prompt, render_whales_digest_prompt
```

Extend `MomentumAnalysisService.__init__`:

```python
        whale_collector=None,
```

Set:

```python
        self.whale_collector = whale_collector or WhaleCollector(settings)
```

- [ ] **Step 4: Add formatting helpers and methods**

Append helpers near `_format_congress_trade_line`:

```python
def _format_whale_activity_line(activity: dict) -> str:
    holding = activity.get("holding") or {}
    change = activity.get("change") or {}
    status = change.get("status", "HELD")
    value = holding.get("value_usd") or change.get("current_value_usd") or 0
    delta = change.get("value_delta_usd")
    delta_text = f", delta ${delta:,}" if isinstance(delta, int) else ""
    return (
        f"- {activity.get('fund')} ({activity.get('manager')}), report {activity.get('report_period')}: "
        f"{status} position worth ${value:,}{delta_text}"
    )


def _watchlist_summary() -> str:
    return ", ".join(f"{fund.fund_name} ({fund.manager})" for fund in WHALE_WATCHLIST)
```

Append methods to `MomentumAnalysisService`:

```python
    async def whale(self, symbol: str) -> str:
        ticker = normalize_ticker_symbol(symbol)
        if not self.settings.whale_enabled:
            return "Whale tracking is disabled. Set EDGAR_IDENTITY to enable SEC 13F tracking."
        try:
            snapshot = await self.whale_collector.get_or_fetch()
        except Exception:
            logger.exception("Whale data unavailable for ticker=%s", ticker)
            return "Whale data unavailable. SEC EDGAR could not be reached and no local cache is available."

        activities = self.whale_collector.activities_for_ticker(ticker)
        signal = next((item for item in snapshot.signals if item.ticker == ticker), None)
        fund_lines = (
            "\n".join(_format_whale_activity_line(activity) for activity in activities[:25])
            if activities
            else "No top-10 whale activity found in local 13F history."
        )
        labels = signal.labels if signal else []
        prompt = render_whale_ticker_prompt(
            ticker=ticker,
            asset_description=ticker,
            watchlist_summary=_watchlist_summary(),
            fund_lines=fund_lines,
            labels=labels,
            reddit_rank="not checked for this request",
            congress_summary="not checked for this request",
        )
        explanation = await self.llm_provider.generate(prompt)
        return f"**Whale Activity: {ticker}**\n{explanation.strip()}"

    async def whales(self, top_n: int = 5) -> str:
        if not self.settings.whale_enabled:
            return "Whale tracking is disabled. Set EDGAR_IDENTITY to enable SEC 13F tracking."
        try:
            snapshot = await self.whale_collector.get_or_fetch()
        except Exception:
            logger.exception("Whale momentum data unavailable")
            return "Whale data unavailable. SEC EDGAR could not be reached and no local cache is available."

        analyzer = WhaleAnalyzer()
        lines = [analyzer.format_signal_line(signal) for signal in snapshot.signals[:top_n]]
        signal_lines = "\n".join(lines) if lines else "No notable whale 13F momentum this period."
        prompt = render_whales_digest_prompt(signal_lines=signal_lines, top_n=top_n)
        explanation = await self.llm_provider.generate(prompt)
        return f"**Whale Momentum**\n{explanation.strip()}"
```

- [ ] **Step 5: Run service tests and verify pass**

Run:

```bash
uv run pytest tests/test_analysis.py::test_whale_service_calls_llm_with_ticker_prompt tests/test_analysis.py::test_whales_service_calls_llm_with_top_signals -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/finage/analysis.py tests/test_analysis.py
git commit -m "feat: add whale analysis service commands"
```

---

### Task 7: Telegram Commands

**Files:**
- Modify: `src/finage/telegram_bot.py`
- Modify: `tests/test_telegram_bot.py`

- [ ] **Step 1: Add failing Telegram tests**

Append to `tests/test_telegram_bot.py`:

```python
async def test_help_mentions_whale_commands(bot: TelegramDigestBot, update, context) -> None:
    await bot.help(update, context)

    assert "/whale <stock>" in update.effective_message.replies[0]
    assert "/whales" in update.effective_message.replies[0]


async def test_whale_requires_one_argument(bot: TelegramDigestBot, update, context) -> None:
    context.args = []

    await bot.whale(update, context)

    assert update.effective_message.replies[-1] == "Usage: /whale TSLA"


async def test_whales_returns_markdown_response(monkeypatch, bot: TelegramDigestBot, update, context) -> None:
    async def fake_whales(self):
        return "**Whale Momentum**\n- NVDA"

    monkeypatch.setattr(MomentumAnalysisService, "whales", fake_whales)

    await bot.whales(update, context)

    assert context.bot.sent_messages[-1]["parse_mode"] == "HTML"
    assert "Whale Momentum" in context.bot.sent_messages[-1]["text"]
```

- [ ] **Step 2: Run Telegram tests to verify failure**

Run:

```bash
uv run pytest tests/test_telegram_bot.py -v
```

Expected: FAIL because `/whale` and `/whales` handlers do not exist.

- [ ] **Step 3: Register commands and update help text**

In `TelegramDigestBot.run()`, add:

```python
        application.add_handler(CommandHandler("whale", self.whale))
        application.add_handler(CommandHandler("whales", self.whales))
```

Update `/start` text to include:

```python
            "/whale TSLA for institutional 13F activity, /whales for top whale momentum, "
```

Update `/help` text to include:

```python
            "/whale <stock> - analyze top-10 institutional 13F activity for one ticker.\n"
            "/whales - summarize top whale momentum across the watchlist.\n"
```

- [ ] **Step 4: Add Telegram handlers**

Add methods near `senate()`:

```python
    async def whale(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return

        if len(context.args) != 1:
            await update.effective_message.reply_text("Usage: /whale TSLA")
            return

        try:
            symbol = normalize_ticker_symbol(context.args[0])
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc).replace("/ticker", "/whale"))
            return

        await update.effective_message.reply_text(f"Analyzing whale 13F activity for {symbol}...")
        try:
            brief = await MomentumAnalysisService(self.settings).whale(symbol)
            await send_markdown_text(context.bot, update.effective_chat.id, brief)
        except Exception:
            logger.exception("Failed to generate whale brief")
            await update.effective_message.reply_text("Whale analysis failed. Check the Pi logs.")

    async def whales(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return

        await update.effective_message.reply_text("Analyzing top whale 13F momentum...")
        try:
            brief = await MomentumAnalysisService(self.settings).whales()
            await send_markdown_text(context.bot, update.effective_chat.id, brief)
        except Exception:
            logger.exception("Failed to generate whales brief")
            await update.effective_message.reply_text("Whales analysis failed. Check the Pi logs.")
```

- [ ] **Step 5: Run Telegram tests and verify pass**

Run:

```bash
uv run pytest tests/test_telegram_bot.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/finage/telegram_bot.py tests/test_telegram_bot.py
git commit -m "feat: add whale telegram commands"
```

---

### Task 8: Digest And CLI Integration

**Files:**
- Modify: `src/finage/digest.py`
- Modify: `src/finage/cli.py`
- Modify: `tests/test_digest.py`
- Modify: `tests/test_whale.py`

- [ ] **Step 1: Add failing digest integration test**

Append to `tests/test_digest.py`:

```python
from finage.models import WhaleSignal, WhaleSnapshot


class FakeWhaleCollector:
    async def get_or_fetch(self):
        return WhaleSnapshot(
            signals=[
                WhaleSignal(
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
                    has_social_overlap=True,
                    labels=["NEW POSITION", "WHALE + SOCIAL"],
                )
            ]
        )


async def test_digest_service_appends_whale_context(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, edgar_identity="finage@example.com")
    llm = FakeLlmProvider("digest")
    service = DigestService(
        settings,
        collector=FakeCollector(make_snapshot()),
        llm_provider=llm,
        whale_collector=FakeWhaleCollector(),
    )

    await service.generate()

    assert "--- WHALE 13F DATA ---" in llm.prompts[0]
    assert "NVDA" in llm.prompts[0]
```

- [ ] **Step 2: Add failing CLI parser test**

Append to `tests/test_whale.py`:

```python
from finage.cli import build_parser


def test_cli_parses_whale_refresh_command() -> None:
    args = build_parser().parse_args(["whale", "refresh"])

    assert args.command == "whale"
    assert args.whale_command == "refresh"
```

- [ ] **Step 3: Run integration tests to verify failure**

Run:

```bash
uv run pytest tests/test_digest.py::test_digest_service_appends_whale_context tests/test_whale.py::test_cli_parses_whale_refresh_command -v
```

Expected: FAIL because `DigestService` does not accept `whale_collector` and CLI does not have `whale`.

- [ ] **Step 4: Modify `digest.py`**

Add imports:

```python
from finage.prompting import render_whale_digest_prompt
from finage.whale import WhaleAnalyzer, WhaleCollector
```

Add protocol:

```python
class WhaleDataSource(Protocol):
    async def get_or_fetch(self):
        raise NotImplementedError
```

Extend `DigestService.__init__`:

```python
        whale_collector: WhaleDataSource | None = None,
```

Set:

```python
        self.whale_collector = whale_collector or WhaleCollector(settings)
```

Add method:

```python
    async def _whale_prompt_section(self, snapshot: WsbSnapshot) -> str:
        if not self.settings.whale_enabled:
            return ""
        try:
            whale_snapshot = await self.whale_collector.get_or_fetch()
        except Exception:
            logger.exception("Whale enrichment failed; continuing without whale section")
            return ""

        analyzer = WhaleAnalyzer()
        signals = analyzer.apply_convergence(
            whale_snapshot.signals,
            reddit_tickers=snapshot.trending_tickers,
            congress_signals=None,
        )
        lines = [analyzer.format_signal_line(signal) for signal in signals[:5]]
        signal_lines = "\n".join(lines) if lines else "No notable whale 13F momentum this period."
        return render_whale_digest_prompt(signal_lines=signal_lines)
```

In `generate()`, after congress prompt append:

```python
        whale_prompt = await self._whale_prompt_section(snapshot)
        if whale_prompt:
            prompt = f"{prompt}\n\n{whale_prompt}"
```

- [ ] **Step 5: Modify `cli.py`**

Add import:

```python
from finage.whale import WhaleCollector
```

In `build_parser()`, add:

```python
    whale_parser = subparsers.add_parser("whale", help="Manage institutional 13F whale data")
    whale_subparsers = whale_parser.add_subparsers(dest="whale_command", required=True)
    whale_subparsers.add_parser("refresh", help="Fetch and persist latest 13F whale data")
```

Add runner:

```python
async def _run_whale_command(settings: Settings, whale_command: str) -> int:
    if whale_command == "refresh":
        collector = WhaleCollector(settings)
        snapshot = await collector.refresh()
        print(
            "Whale refresh complete: "
            f"funds={len(snapshot.funds)} "
            f"tickers={len({holding.ticker for fund in snapshot.funds for holding in fund.holdings})} "
            f"signals={len(snapshot.signals)} "
            f"cache={collector.base_dir}"
        )
        return 0

    raise ValueError(f"Unsupported whale command: {whale_command}")
```

In `main()`:

```python
    if args.command == "whale":
        return asyncio.run(_run_whale_command(settings, args.whale_command))
```

- [ ] **Step 6: Run integration tests and verify pass**

Run:

```bash
uv run pytest tests/test_digest.py::test_digest_service_appends_whale_context tests/test_whale.py::test_cli_parses_whale_refresh_command -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/finage/digest.py src/finage/cli.py tests/test_digest.py tests/test_whale.py
git commit -m "feat: add whale digest and cli integration"
```

---

### Task 9: Final Verification And Manual Test Path

**Files:**
- No new files.

- [ ] **Step 1: Run focused tests**

Run:

```bash
uv run pytest tests/test_whale.py tests/test_analysis.py tests/test_digest.py tests/test_telegram_bot.py -v
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run:

```bash
uv run pytest tests -v
```

Expected: PASS.

- [ ] **Step 3: Run static import smoke test**

Run:

```bash
uv run python - <<'PY'
from finage.whale import WHALE_WATCHLIST, WhaleCollector, WhaleAnalyzer
from finage.settings import Settings
print(len(WHALE_WATCHLIST), WhaleCollector.__name__, WhaleAnalyzer.__name__)
PY
```

Expected output contains:

```text
10 WhaleCollector WhaleAnalyzer
```

- [ ] **Step 4: Manual local refresh with real SEC data**

Set `EDGAR_IDENTITY` to a valid email or user-agent identity, then run:

```bash
EDGAR_IDENTITY="Your Name your.email@example.com" uv run finage whale refresh
```

Expected output:

```text
Whale refresh complete: funds=<1-10> tickers=<number> signals=<number> cache=data/whale
```

Verify files:

```bash
ls data/whale data/whale/by_fund data/whale/by_ticker
```

Expected:
- `data/whale/latest.json`
- `data/whale/signals.json`
- at least one `data/whale/by_fund/*.json`
- at least one `data/whale/by_ticker/*.json`

- [ ] **Step 5: Manual Telegram command checks**

With the bot running and `EDGAR_IDENTITY` configured:

```text
/whales
/whale NVDA
/whale TSLA
```

Expected:
- `/whales` returns a brief headed `Whale Momentum`.
- `/whale NVDA` returns a brief headed `Whale Activity: NVDA`.
- If a ticker has no top-10 fund activity, Gemini still explains that local 13F history has no top-10 whale activity and includes the quarterly delay caveat.

- [ ] **Step 6: Commit final fixes if any**

If verification required follow-up edits:

```bash
git add <changed files>
git commit -m "fix: stabilize whale tracking integration"
```

If no follow-up edits were needed, do not create an empty commit.

---

## Self-Review Notes

- Spec coverage: settings, edgartools identity, top-10 watchlist, local storage, scoring, convergence, `/whale`, `/whales`, digest, CLI, and tests are all mapped to tasks.
- Type consistency: plan uses `WhaleHolding`, `WhaleChange`, `WhaleFundSnapshot`, `WhaleSignal`, and `WhaleSnapshot` consistently across models, collector, analyzer, service, and tests.
- Known implementation caution: edgartools DataFrame column names may vary by version. The normalizers intentionally accept multiple likely names via `_first_present`; if manual refresh exposes a new column name, extend `_first_present` call sites and add a fixture test.
- Commit guidance: each task is independently testable and commit-sized.
