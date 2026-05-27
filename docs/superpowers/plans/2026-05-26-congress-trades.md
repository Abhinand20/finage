# Congress Trades Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add FMP-powered congressional trade ingestion, scoring, digest enrichment, `/senate <TICKER>`, and a daily refresh CLI to finage.

**Architecture:** Create a focused `finage.congress` module that owns FMP fetching, normalization, deduplication, storage, and deterministic analysis. Existing modules stay thin: `digest.py` injects precomputed congressional context into the Gemini prompt, `analysis.py` adds `/senate` and live badges, `telegram_bot.py` wires the command, and `cli.py` exposes `finage congress refresh`.

**Tech Stack:** Python 3.11, Pydantic v2, httpx async client, pytest, pytest-asyncio, Gemini through existing `LlmProvider`, Telegram through existing `python-telegram-bot` handlers.

---

## File Structure

Create:
- `src/finage/congress.py` - all congressional trade fetching, normalization, local storage, scoring, and format helpers.
- `src/finage/prompts/congress_ticker.md` - prompt template for `/senate <TICKER>`.
- `src/finage/prompts/congress_digest.md` - prompt fragment for deterministic digest enrichment.
- `tests/test_congress.py` - collector, normalization, storage, scoring, pagination, and cache fallback tests.
- `docs/superpowers/plans/2026-05-26-congress-trades.md` - this implementation plan.

Modify:
- `src/finage/models.py` - add `CongressTrade`, `CongressSnapshot`, `CongressSignal`, `CongressFetchMetadata`.
- `src/finage/settings.py` - add FMP and congress settings.
- `src/finage/prompting.py` - add prompt rendering helpers for congressional digest and ticker analysis.
- `src/finage/digest.py` - fetch congress data and append deterministic congressional context to the digest prompt.
- `src/finage/analysis.py` - add `/senate` analysis and optional live congress badges.
- `src/finage/telegram_bot.py` - register and implement `/senate`.
- `src/finage/cli.py` - add `finage congress refresh`.
- `.env.example` - document FMP and congress settings.
- `tests/test_digest.py` - cover prompt enrichment and disabled behavior.
- `tests/test_analysis.py` - cover `/senate` and live badge formatting.
- `tests/test_telegram_bot.py` - cover Telegram handler behavior.

Do not modify:
- `brainstorm.md` unless the user explicitly asks. It is already dirty and unrelated.

---

### Task 1: Models And Settings

**Files:**
- Modify: `src/finage/models.py`
- Modify: `src/finage/settings.py`
- Test: `tests/test_congress.py`

- [ ] **Step 1: Write failing model and settings tests**

Create `tests/test_congress.py` with these initial tests:

```python
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from finage.models import CongressFetchMetadata, CongressSignal, CongressSnapshot, CongressTrade
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
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/test_congress.py -v
```

Expected: FAIL with an import error for `CongressTrade` or `CongressFetchMetadata`.

- [ ] **Step 3: Add congress models**

Modify `src/finage/models.py` imports:

```python
from datetime import UTC, date, datetime
from typing import Literal
```

Keep existing models unchanged and append these classes after `DigestResult`:

```python
class CongressTrade(BaseModel):
    trade_id: str
    source_hash: str
    ticker: str
    asset_description: str
    representative: str
    chamber: Literal["Senate", "House"]
    transaction_type: Literal["Bought", "Sold", "Exchange"]
    raw_type: str
    amount: str
    amount_min: int | None = None
    amount_max: int | None = None
    amount_midpoint: int | None = None
    raw_amount: str
    transaction_date: date
    disclosure_date: date
    disclosure_lag_days: int
    first_seen_at: datetime
    last_seen_at: datetime


class CongressSnapshot(BaseModel):
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    total_trades: int = 0
    new_trades: int = 0
    corrected_trades: int = 0
    last_successful_fetch_at: datetime | None = None
    trades: list[CongressTrade] = Field(default_factory=list)


class CongressFetchMetadata(BaseModel):
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_successful_fetch_at: datetime | None = None
    senate_pages: int = 0
    house_pages: int = 0
    total_records: int = 0
    new_trades: int = 0
    corrected_trades: int = 0


class CongressSignal(BaseModel):
    ticker: str
    asset_description: str
    total_score: float
    net_buy_score: float
    buy_count: int
    sell_count: int
    trade_count: int
    new_trade_count: int
    unique_politicians: int
    total_bought_value_midpoint: int
    total_sold_value_midpoint: int
    largest_amount: str
    politicians: list[str] = Field(default_factory=list)
    latest_disclosure: date
    days_since_disclosure: int
    avg_disclosure_lag_days: float
    is_cluster: bool
    is_bicameral: bool
    convergence_score: float | None = None
```

- [ ] **Step 4: Add settings fields**

Modify `src/finage/settings.py` `Settings` fields after web search settings:

```python
    fmp_api_key: str | None = None
    congress_lookback_days: int = 30
    congress_bootstrap_days: int = 180
    congress_max_pages_per_refresh: int = 4
    congress_cache_ttl_hours: int = 12
    congress_min_signal_score: float = 2.0
    congress_enabled: bool = True
```

Update `model_post_init`:

```python
        if not self.fmp_api_key:
            object.__setattr__(self, "congress_enabled", False)
```

Update `Settings.from_env()` kwargs:

```python
            "fmp_api_key": _optional_str(os.getenv("FMP_API_KEY")),
            "congress_lookback_days": _int_env("CONGRESS_LOOKBACK_DAYS", 30),
            "congress_bootstrap_days": _int_env("CONGRESS_BOOTSTRAP_DAYS", 180),
            "congress_max_pages_per_refresh": _int_env("CONGRESS_MAX_PAGES_PER_REFRESH", 4),
            "congress_cache_ttl_hours": _int_env("CONGRESS_CACHE_TTL_HOURS", 12),
            "congress_min_signal_score": float(os.getenv("CONGRESS_MIN_SIGNAL_SCORE", "2.0")),
```

- [ ] **Step 5: Run tests and verify pass**

Run:

```bash
uv run pytest tests/test_congress.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/finage/models.py src/finage/settings.py tests/test_congress.py
git commit -m "feat: add congress trade models and settings"
```

---

### Task 2: Normalization And Scoring

**Files:**
- Create: `src/finage/congress.py`
- Modify: `tests/test_congress.py`

- [ ] **Step 1: Add failing normalization and scoring tests**

Append to `tests/test_congress.py`:

```python
from finage.congress import (
    CongressAnalyzer,
    amount_weight,
    normalize_congress_trade,
    parse_amount_range,
)
from finage.models import PostEvidence, TickerEvidence, TrendingTicker


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
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/test_congress.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'finage.congress'`.

- [ ] **Step 3: Create `src/finage/congress.py` with deterministic analysis helpers**

Create `src/finage/congress.py`:

```python
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from finage.models import CongressSignal, CongressTrade, TickerEvidence, TrendingTicker

AMOUNT_RE = re.compile(r"\$?([0-9,]+)")


@dataclass(frozen=True)
class ParsedAmount:
    label: str
    amount_min: int | None
    amount_max: int | None
    amount_midpoint: int | None


def _parse_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def parse_amount_range(raw_amount: str) -> ParsedAmount:
    raw = (raw_amount or "").strip()
    numbers = [int(match.replace(",", "")) for match in AMOUNT_RE.findall(raw)]
    if len(numbers) >= 2:
        amount_min = numbers[0]
        amount_max = numbers[1]
        midpoint = (amount_min + amount_max) // 2
    elif numbers:
        amount_min = numbers[0]
        amount_max = None
        midpoint = amount_min
    else:
        return ParsedAmount(label=raw or "Unknown", amount_min=None, amount_max=None, amount_midpoint=None)

    if midpoint >= 5_000_000:
        label = "Over $5M"
    elif midpoint >= 1_000_000:
        label = "$1M-$5M"
    elif midpoint >= 500_000:
        label = "$500K-$1M"
    elif midpoint >= 250_000:
        label = "$250K-$500K"
    elif midpoint >= 100_000:
        label = "$100K-$250K"
    elif midpoint >= 50_000:
        label = "$50K-$100K"
    elif midpoint >= 15_000:
        label = "$15K-$50K"
    else:
        label = "$1K-$15K"

    return ParsedAmount(label=label, amount_min=amount_min, amount_max=amount_max, amount_midpoint=midpoint)


def normalize_transaction_type(raw_type: str) -> Literal["Bought", "Sold", "Exchange"]:
    lowered = (raw_type or "").lower()
    if "purchase" in lowered or "buy" in lowered:
        return "Bought"
    if "sale" in lowered or "sell" in lowered:
        return "Sold"
    return "Exchange"


def make_trade_id(
    *,
    chamber: str,
    ticker: str,
    representative: str,
    asset_description: str,
    transaction_date: date,
    disclosure_date: date,
    raw_type: str,
    raw_amount: str,
) -> str:
    identity = (
        f"{chamber}|{ticker}|{representative}|{asset_description}|"
        f"{transaction_date.isoformat()}|{disclosure_date.isoformat()}|{raw_type}|{raw_amount}"
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def make_source_hash(payload: dict) -> str:
    normalized = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def normalize_congress_trade(
    payload: dict,
    *,
    chamber: Literal["Senate", "House"],
    seen_at: datetime | None = None,
) -> CongressTrade:
    seen = seen_at or datetime.now(UTC)
    ticker = str(payload.get("symbol", "")).upper().strip()
    representative = f"{payload.get('firstName', '')} {payload.get('lastName', '')}".strip()
    raw_type = str(payload.get("type", "")).strip()
    raw_amount = str(payload.get("amount", "")).strip()
    amount = parse_amount_range(raw_amount)
    transaction_date = _parse_date(str(payload["transactionDate"]))
    disclosure_date = _parse_date(str(payload["disclosureDate"]))
    asset_description = str(payload.get("assetDescription", "")).strip()

    trade_id = make_trade_id(
        chamber=chamber,
        ticker=ticker,
        representative=representative,
        asset_description=asset_description,
        transaction_date=transaction_date,
        disclosure_date=disclosure_date,
        raw_type=raw_type,
        raw_amount=raw_amount,
    )

    return CongressTrade(
        trade_id=trade_id,
        source_hash=make_source_hash({**payload, "chamber": chamber}),
        ticker=ticker,
        asset_description=asset_description,
        representative=representative,
        chamber=chamber,
        transaction_type=normalize_transaction_type(raw_type),
        raw_type=raw_type,
        amount=amount.label,
        amount_min=amount.amount_min,
        amount_max=amount.amount_max,
        amount_midpoint=amount.amount_midpoint,
        raw_amount=raw_amount,
        transaction_date=transaction_date,
        disclosure_date=disclosure_date,
        disclosure_lag_days=max(0, (disclosure_date - transaction_date).days),
        first_seen_at=seen,
        last_seen_at=seen,
    )


def amount_weight(amount_midpoint: int | None) -> float:
    if amount_midpoint is None:
        return 0.5
    if amount_midpoint >= 5_000_000:
        return 6.0
    if amount_midpoint >= 1_000_000:
        return 5.0
    if amount_midpoint >= 500_000:
        return 3.5
    if amount_midpoint >= 250_000:
        return 2.5
    if amount_midpoint >= 100_000:
        return 2.0
    if amount_midpoint >= 50_000:
        return 1.5
    if amount_midpoint >= 15_000:
        return 1.0
    return 0.5


def type_weight(transaction_type: str) -> float:
    if transaction_type == "Bought":
        return 1.5
    if transaction_type == "Sold":
        return 0.7
    return 1.0


def recency_weight(disclosure_date: date, *, today: date) -> float:
    age_days = max(0, (today - disclosure_date).days)
    if age_days <= 7:
        return 1.5
    if age_days <= 30:
        return 1.0
    if age_days <= 60:
        return 0.6
    return 0.3


def _cluster_bonus(buy_count: int) -> float:
    if buy_count >= 3:
        return 2.0
    if buy_count == 2:
        return 1.5
    return 1.0


def _largest_amount(trades: list[CongressTrade]) -> str:
    trade = max(trades, key=lambda item: item.amount_midpoint or 0)
    return trade.amount


class CongressAnalyzer:
    def __init__(self, *, today: date | None = None):
        self.today = today or datetime.now(UTC).date()

    def _trade_score(self, trade: CongressTrade) -> float:
        return (
            amount_weight(trade.amount_midpoint)
            * type_weight(trade.transaction_type)
            * recency_weight(trade.disclosure_date, today=self.today)
        )

    def top_signals(
        self,
        trades: list[CongressTrade],
        new_trades: list[CongressTrade],
        lookback_days: int,
        min_score: float,
    ) -> list[CongressSignal]:
        new_ids = {trade.trade_id for trade in new_trades}
        grouped: dict[str, list[CongressTrade]] = defaultdict(list)
        for trade in trades:
            if (self.today - trade.disclosure_date).days <= lookback_days:
                grouped[trade.ticker].append(trade)

        signals: list[CongressSignal] = []
        for ticker, ticker_trades in grouped.items():
            buy_count = sum(1 for trade in ticker_trades if trade.transaction_type == "Bought")
            sell_count = sum(1 for trade in ticker_trades if trade.transaction_type == "Sold")
            base_score = sum(self._trade_score(trade) for trade in ticker_trades)
            total_score = base_score * _cluster_bonus(buy_count)
            if total_score < min_score:
                continue

            bought_value = sum(
                trade.amount_midpoint or 0 for trade in ticker_trades if trade.transaction_type == "Bought"
            )
            sold_value = sum(
                trade.amount_midpoint or 0 for trade in ticker_trades if trade.transaction_type == "Sold"
            )
            buy_score = sum(self._trade_score(trade) for trade in ticker_trades if trade.transaction_type == "Bought")
            sell_score = sum(self._trade_score(trade) for trade in ticker_trades if trade.transaction_type == "Sold")
            latest_disclosure = max(trade.disclosure_date for trade in ticker_trades)
            politicians = sorted({trade.representative for trade in ticker_trades})
            chambers = {trade.chamber for trade in ticker_trades}

            signals.append(
                CongressSignal(
                    ticker=ticker,
                    asset_description=ticker_trades[0].asset_description,
                    total_score=round(total_score, 2),
                    net_buy_score=round(buy_score - sell_score, 2),
                    buy_count=buy_count,
                    sell_count=sell_count,
                    trade_count=len(ticker_trades),
                    new_trade_count=sum(1 for trade in ticker_trades if trade.trade_id in new_ids),
                    unique_politicians=len(politicians),
                    total_bought_value_midpoint=bought_value,
                    total_sold_value_midpoint=sold_value,
                    largest_amount=_largest_amount(ticker_trades),
                    politicians=politicians,
                    latest_disclosure=latest_disclosure,
                    days_since_disclosure=max(0, (self.today - latest_disclosure).days),
                    avg_disclosure_lag_days=round(
                        sum(trade.disclosure_lag_days for trade in ticker_trades) / len(ticker_trades),
                        1,
                    ),
                    is_cluster=buy_count >= 2,
                    is_bicameral=len(chambers) > 1,
                )
            )

        return sorted(signals, key=lambda item: item.total_score, reverse=True)

    def overlap_tickers(self, signals: list[CongressSignal], reddit_tickers: list[str]) -> set[str]:
        return {signal.ticker for signal in signals} & {ticker.upper() for ticker in reddit_tickers}

    def apply_convergence_scores(
        self,
        signals: list[CongressSignal],
        reddit_tickers: list[TrendingTicker],
        evidence_by_ticker: dict[str, TickerEvidence],
    ) -> list[CongressSignal]:
        rank_by_ticker = {item.ticker: item.rank for item in reddit_tickers}
        enriched: list[CongressSignal] = []
        for signal in signals:
            rank = rank_by_ticker.get(signal.ticker)
            if rank is None:
                signal.convergence_score = None
            else:
                if rank <= 3:
                    rank_score = 3.0
                elif rank <= 10:
                    rank_score = 2.0
                elif rank <= 25:
                    rank_score = 1.0
                else:
                    rank_score = 0.0
                evidence = evidence_by_ticker.get(signal.ticker)
                evidence_score = min(3.0, (evidence.evidence_score / 1000.0) if evidence else 0.0)
                signal.convergence_score = round(signal.total_score + rank_score + evidence_score, 2)
            enriched.append(signal)

        return sorted(
            enriched,
            key=lambda item: (item.convergence_score if item.convergence_score is not None else item.total_score),
            reverse=True,
        )

    def format_signal_line(self, signal: CongressSignal, overlap: bool) -> str:
        labels = []
        if signal.new_trade_count:
            labels.append("NEW")
        if signal.is_cluster:
            labels.append("Cluster")
        if signal.is_bicameral:
            labels.append("Bicameral")
        if overlap:
            labels.append("CONVERGENCE")

        direction = f"{signal.buy_count} buys"
        if signal.sell_count:
            direction = f"{direction}, {signal.sell_count} sales"
        politician_text = ", ".join(signal.politicians[:3])
        label_text = f" · {' · '.join(labels)}" if labels else ""
        return (
            f"- **{signal.ticker}** — {direction} · est. ${signal.total_bought_value_midpoint:,} bought · "
            f"largest {signal.largest_amount} · {politician_text}{label_text}"
        )
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
uv run pytest tests/test_congress.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finage/congress.py tests/test_congress.py
git commit -m "feat: add congress trade normalization and scoring"
```

---

### Task 3: Local Storage, Deduplication, And Correction Tracking

**Files:**
- Modify: `src/finage/congress.py`
- Modify: `tests/test_congress.py`

- [ ] **Step 1: Add failing storage tests**

Append to `tests/test_congress.py`:

```python
from finage.congress import CongressCollector


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
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/test_congress.py -v
```

Expected: FAIL with `AttributeError: 'CongressCollector' object has no attribute 'merge_and_persist'`.

- [ ] **Step 3: Add storage methods to `CongressCollector`**

Append this class implementation to `src/finage/congress.py` after `CongressAnalyzer`:

```python
from pathlib import Path

from finage.models import CongressFetchMetadata, CongressSnapshot
from finage.settings import Settings


class CongressCollector:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_dir = settings.data_dir / "congress"
        self.by_ticker_dir = self.base_dir / "by_ticker"

    @property
    def history_path(self) -> Path:
        return self.base_dir / "history.json"

    @property
    def latest_path(self) -> Path:
        return self.base_dir / "latest.json"

    @property
    def latest_fetch_path(self) -> Path:
        return self.base_dir / "latest_fetch.json"

    @property
    def new_trades_path(self) -> Path:
        return self.base_dir / "new_trades.json"

    def _write_json(self, path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    def get_cached(self) -> CongressSnapshot | None:
        if not self.history_path.exists():
            return None
        return CongressSnapshot.model_validate_json(self.history_path.read_text(encoding="utf-8"))

    def read_new_trades(self) -> list[CongressTrade]:
        if not self.new_trades_path.exists():
            return []
        data = json.loads(self.new_trades_path.read_text(encoding="utf-8"))
        return [CongressTrade.model_validate(item) for item in data]

    def _write_ticker_files(self, trades: list[CongressTrade], tickers: set[str] | None = None) -> None:
        grouped: dict[str, list[CongressTrade]] = defaultdict(list)
        for trade in trades:
            grouped[trade.ticker].append(trade)

        target_tickers = tickers or set(grouped)
        self.by_ticker_dir.mkdir(parents=True, exist_ok=True)
        for ticker in target_tickers:
            ticker_trades = sorted(
                grouped.get(ticker, []),
                key=lambda item: (item.disclosure_date, item.transaction_date),
                reverse=True,
            )
            path = self.by_ticker_dir / f"{ticker}.json"
            self._write_json(
                path,
                json.dumps([trade.model_dump(mode="json") for trade in ticker_trades], indent=2),
            )

    def trades_for_ticker(self, snapshot: CongressSnapshot, ticker: str) -> list[CongressTrade]:
        symbol = ticker.upper().strip()
        path = self.by_ticker_dir / f"{symbol}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return [CongressTrade.model_validate(item) for item in data]
        return [trade for trade in snapshot.trades if trade.ticker == symbol]

    def merge_and_persist(
        self,
        fetched_trades: list[CongressTrade],
        *,
        fetched_at: datetime,
        senate_pages: int,
        house_pages: int,
    ) -> CongressSnapshot:
        existing_snapshot = self.get_cached()
        existing_trades = existing_snapshot.trades if existing_snapshot else []
        existing_by_id = {trade.trade_id: trade for trade in existing_trades}

        new_trades: list[CongressTrade] = []
        corrected = 0
        changed_tickers: set[str] = set()

        for trade in fetched_trades:
            existing = existing_by_id.get(trade.trade_id)
            if existing is None:
                new_trades.append(trade)
                existing_by_id[trade.trade_id] = trade
                changed_tickers.add(trade.ticker)
                continue
            if existing.source_hash != trade.source_hash:
                corrected += 1
                existing_by_id[trade.trade_id] = trade.model_copy(
                    update={"first_seen_at": existing.first_seen_at, "last_seen_at": fetched_at}
                )
                changed_tickers.add(trade.ticker)

        all_trades = sorted(
            existing_by_id.values(),
            key=lambda item: (item.disclosure_date, item.transaction_date, item.ticker),
            reverse=True,
        )
        snapshot = CongressSnapshot(
            fetched_at=fetched_at,
            total_trades=len(all_trades),
            new_trades=len(new_trades),
            corrected_trades=corrected,
            last_successful_fetch_at=fetched_at,
            trades=all_trades,
        )
        metadata = CongressFetchMetadata(
            fetched_at=fetched_at,
            last_successful_fetch_at=fetched_at,
            senate_pages=senate_pages,
            house_pages=house_pages,
            total_records=len(fetched_trades),
            new_trades=len(new_trades),
            corrected_trades=corrected,
        )

        self._write_json(self.history_path, snapshot.model_dump_json(indent=2))
        self._write_json(self.latest_path, snapshot.model_dump_json(indent=2))
        self._write_json(self.latest_fetch_path, metadata.model_dump_json(indent=2))
        self._write_json(
            self.new_trades_path,
            json.dumps([trade.model_dump(mode="json") for trade in new_trades], indent=2),
        )
        self._write_ticker_files(all_trades, changed_tickers)
        return snapshot
```

- [ ] **Step 4: Move imports to the top**

After adding the class, move `from pathlib import Path`, `CongressFetchMetadata`, `CongressSnapshot`, and `Settings` imports to the top of `src/finage/congress.py` so the file has imports before executable code. The final top import block should include:

```python
from pathlib import Path

from finage.models import (
    CongressFetchMetadata,
    CongressSignal,
    CongressSnapshot,
    CongressTrade,
    TickerEvidence,
    TrendingTicker,
)
from finage.settings import Settings
```

- [ ] **Step 5: Run tests and verify pass**

Run:

```bash
uv run pytest tests/test_congress.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/finage/congress.py tests/test_congress.py
git commit -m "feat: persist congress trade history"
```

---

### Task 4: FMP Fetching, Pagination, Cache TTL, And Fallback

**Files:**
- Modify: `src/finage/congress.py`
- Modify: `tests/test_congress.py`

- [ ] **Step 1: Add failing async fetch and cache tests**

Append to `tests/test_congress.py`:

```python
import httpx
import pytest


class FakeFmpTransport:
    def __init__(self, pages: dict[tuple[str, int], list[dict]]):
        self.pages = pages
        self.calls: list[tuple[str, int]] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        endpoint = "senate" if "senate-latest" in str(request.url) else "house"
        page = int(request.url.params.get("page", "0"))
        self.calls.append((endpoint, page))
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
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/test_congress.py -v
```

Expected: FAIL because `CongressCollector.__init__` does not accept `client_factory` and `now_provider`.

- [ ] **Step 3: Implement FMP fetch and TTL logic**

Modify `CongressCollector.__init__`:

```python
    def __init__(
        self,
        settings: Settings,
        *,
        client_factory=None,
        now_provider=None,
    ):
        self.settings = settings
        self.base_dir = settings.data_dir / "congress"
        self.by_ticker_dir = self.base_dir / "by_ticker"
        self.client_factory = client_factory
        self.now_provider = now_provider or (lambda: datetime.now(UTC))
```

Add methods inside `CongressCollector`:

```python
    def _client(self):
        import httpx

        if self.client_factory is not None:
            return self.client_factory()
        return httpx.AsyncClient(timeout=20)

    def _is_cache_fresh(self, snapshot: CongressSnapshot) -> bool:
        fetched_at = snapshot.last_successful_fetch_at or snapshot.fetched_at
        age_hours = (self.now_provider() - fetched_at).total_seconds() / 3600
        return age_hours < self.settings.congress_cache_ttl_hours

    async def get_or_fetch(self) -> CongressSnapshot:
        cached = self.get_cached()
        if cached is not None and self._is_cache_fresh(cached):
            return cached
        try:
            return await self.fetch(bootstrap=cached is None)
        except Exception:
            if cached is not None:
                return cached
            raise

    async def fetch(self, *, bootstrap: bool = False) -> CongressSnapshot:
        if not self.settings.fmp_api_key:
            raise RuntimeError("FMP_API_KEY is required for congress data")
        fetched_at = self.now_provider()
        cached = self.get_cached()
        last_successful_fetch_at = cached.last_successful_fetch_at if cached else None

        async with self._client() as client:
            senate_trades, senate_pages = await self._fetch_chamber(
                client,
                chamber="Senate",
                endpoint="https://financialmodelingprep.com/stable/senate-latest",
                fetched_at=fetched_at,
                bootstrap=bootstrap,
                last_successful_fetch_at=last_successful_fetch_at,
            )
            house_trades, house_pages = await self._fetch_chamber(
                client,
                chamber="House",
                endpoint="https://financialmodelingprep.com/stable/house-latest",
                fetched_at=fetched_at,
                bootstrap=bootstrap,
                last_successful_fetch_at=last_successful_fetch_at,
            )

        return self.merge_and_persist(
            senate_trades + house_trades,
            fetched_at=fetched_at,
            senate_pages=senate_pages,
            house_pages=house_pages,
        )

    async def _fetch_chamber(
        self,
        client,
        *,
        chamber: Literal["Senate", "House"],
        endpoint: str,
        fetched_at: datetime,
        bootstrap: bool,
        last_successful_fetch_at: datetime | None,
    ) -> tuple[list[CongressTrade], int]:
        trades: list[CongressTrade] = []
        max_pages = self.settings.congress_max_pages_per_refresh
        cutoff_date = (
            fetched_at.date().replace()
            if bootstrap
            else (last_successful_fetch_at.date() if last_successful_fetch_at else fetched_at.date())
        )
        if bootstrap:
            from datetime import timedelta

            cutoff_date = fetched_at.date() - timedelta(days=self.settings.congress_bootstrap_days)
        else:
            from datetime import timedelta

            cutoff_date = cutoff_date - timedelta(days=1)

        pages_fetched = 0
        for page in range(max_pages):
            response = await client.get(
                endpoint,
                params={
                    "page": page,
                    "limit": 250,
                    "apikey": self.settings.fmp_api_key,
                },
            )
            response.raise_for_status()
            rows = response.json()
            pages_fetched += 1
            if not rows:
                break

            page_trades = [
                normalize_congress_trade(row, chamber=chamber, seen_at=fetched_at)
                for row in rows
                if row.get("symbol") and row.get("transactionDate") and row.get("disclosureDate")
            ]
            trades.extend(page_trades)
            oldest_disclosure = min((trade.disclosure_date for trade in page_trades), default=cutoff_date)
            if oldest_disclosure < cutoff_date:
                break

        return trades, pages_fetched
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
uv run pytest tests/test_congress.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finage/congress.py tests/test_congress.py
git commit -m "feat: fetch congress trades from FMP"
```

---

### Task 5: Prompt Helpers For Digest And `/senate`

**Files:**
- Create: `src/finage/prompts/congress_digest.md`
- Create: `src/finage/prompts/congress_ticker.md`
- Modify: `src/finage/prompting.py`
- Modify: `tests/test_congress.py`

- [ ] **Step 1: Add failing prompt rendering tests**

Append to `tests/test_congress.py`:

```python
from finage.prompting import render_congress_digest_prompt, render_congress_ticker_prompt


def test_render_congress_digest_prompt_preserves_deterministic_labels() -> None:
    prompt = render_congress_digest_prompt(
        congress_section_markdown="- **NVDA** — NEW · 2 buys · Cluster · CONVERGENCE",
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
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/test_congress.py -v
```

Expected: FAIL with import error for `render_congress_digest_prompt`.

- [ ] **Step 3: Add prompt templates**

Create `src/finage/prompts/congress_digest.md`:

```markdown
--- CONGRESSIONAL TRADING DATA ---
Disclosures filed in the last {lookback_days} days. Signals above score threshold only.
Ordering and tags are precomputed by code; do not reorder tickers or invent additional trades.

{congress_section_markdown}

Overlap with social momentum top-10: {overlap_list}

Instructions: After your main momentum analysis, add a "## Congressional Activity" section.
Format it as bullet points, one per ticker. Include politician names, amount ranges, buy/sell direction, estimated dollar exposure, and disclosure lag when available.
Preserve all "NEW", "Cluster", "Bicameral", and "CONVERGENCE" labels.
If no signals exist above the threshold, write a single line:
"No notable congressional activity this period."
--- END CONGRESSIONAL TRADING DATA ---
```

Create `src/finage/prompts/congress_ticker.md`:

```markdown
You are analyzing U.S. congressional trading disclosures for {ticker} ({asset_description}).

Congressional trade history (all available, sorted by disclosure date descending):
{trades_formatted}

Current social momentum context:
- ApeWisdom Reddit rank: {reddit_rank} (or "not in top {ticker_limit}" if absent)

Provide a concise analysis covering:
1. Overall pattern: are members of Congress buying or selling? Any cluster activity?
2. Largest trades and who made them.
3. Signal strength based on the precomputed score, estimated dollar exposure, and disclosure lag.
4. Whether the social momentum context strengthens or weakens the thesis.

If there are no trades, note that and provide general sector/regulatory context for {ticker}.
Do not fabricate missing data.
Do not present this as financial advice.
Keep the response under 400 words.
```

- [ ] **Step 4: Add prompt helper functions**

Append to `src/finage/prompting.py`:

```python
def render_congress_digest_prompt(
    *,
    congress_section_markdown: str,
    lookback_days: int,
    overlap_tickers: list[str],
) -> str:
    template = resources.files("finage.prompts").joinpath("congress_digest.md").read_text(encoding="utf-8")
    overlap_list = ", ".join(overlap_tickers) if overlap_tickers else "None"
    return template.format(
        congress_section_markdown=congress_section_markdown,
        lookback_days=lookback_days,
        overlap_list=overlap_list,
    )


def render_congress_ticker_prompt(
    *,
    ticker: str,
    asset_description: str,
    trades_formatted: str,
    reddit_rank: str,
    ticker_limit: int,
) -> str:
    template = resources.files("finage.prompts").joinpath("congress_ticker.md").read_text(encoding="utf-8")
    return template.format(
        ticker=ticker,
        asset_description=asset_description,
        trades_formatted=trades_formatted,
        reddit_rank=reddit_rank,
        ticker_limit=ticker_limit,
    )
```

- [ ] **Step 5: Run tests and verify pass**

Run:

```bash
uv run pytest tests/test_congress.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/finage/prompts/congress_digest.md src/finage/prompts/congress_ticker.md src/finage/prompting.py tests/test_congress.py
git commit -m "feat: add congress prompt helpers"
```

---

### Task 6: Digest Integration

**Files:**
- Modify: `src/finage/digest.py`
- Modify: `tests/test_digest.py`

- [ ] **Step 1: Add failing digest integration tests**

Append to `tests/test_digest.py`:

```python
from datetime import UTC, date, datetime

from finage.congress import normalize_congress_trade
from finage.models import CongressSnapshot


class FakeCongressCollector:
    def __init__(self, snapshot: CongressSnapshot, new_trades):
        self.snapshot = snapshot
        self.new_trades = new_trades

    async def get_or_fetch(self) -> CongressSnapshot:
        return self.snapshot

    def read_new_trades(self):
        return self.new_trades


@pytest.mark.asyncio
async def test_digest_service_appends_congress_context(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.fmp_api_key = "fmp-key"
    settings.congress_enabled = True
    trade = normalize_congress_trade(
        {
            "symbol": "TSLA",
            "firstName": "Jane",
            "lastName": "Doe",
            "transactionDate": "2026-05-01",
            "disclosureDate": "2026-05-20",
            "type": "Purchase",
            "amount": "$100,001 - $250,000",
            "assetDescription": "Tesla Inc.",
        },
        chamber="House",
        seen_at=datetime(2026, 5, 21, tzinfo=UTC),
    )
    snapshot = CongressSnapshot(
        fetched_at=datetime(2026, 5, 21, tzinfo=UTC),
        total_trades=1,
        new_trades=1,
        corrected_trades=0,
        last_successful_fetch_at=datetime(2026, 5, 21, tzinfo=UTC),
        trades=[trade],
    )
    llm = FakeLlm()
    service = DigestService(
        settings,
        collector=FakeCollector(make_snapshot()),
        llm_provider=llm,
        congress_collector=FakeCongressCollector(snapshot, [trade]),
    )

    await service.generate()

    assert "CONGRESSIONAL TRADING DATA" in llm.last_prompt
    assert "TSLA" in llm.last_prompt
    assert "NEW" in llm.last_prompt
    assert "CONVERGENCE" in llm.last_prompt


@pytest.mark.asyncio
async def test_digest_service_skips_congress_when_disabled(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.congress_enabled = False
    llm = FakeLlm()
    service = DigestService(settings, collector=FakeCollector(make_snapshot()), llm_provider=llm)

    await service.generate()

    assert "CONGRESSIONAL TRADING DATA" not in llm.last_prompt
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/test_digest.py -v
```

Expected: FAIL because `DigestService.__init__` does not accept `congress_collector`.

- [ ] **Step 3: Modify `DigestService`**

Update imports in `src/finage/digest.py`:

```python
from finage.congress import CongressAnalyzer, CongressCollector
from finage.prompting import build_digest_payload, render_congress_digest_prompt, render_digest_prompt
```

Add a protocol near `Collector`:

```python
class CongressDataSource(Protocol):
    async def get_or_fetch(self):
        raise NotImplementedError

    def read_new_trades(self):
        raise NotImplementedError
```

Update `DigestService.__init__` signature and assignment:

```python
        congress_collector: CongressDataSource | None = None,
```

```python
        self.congress_collector = congress_collector or CongressCollector(settings)
```

Add helper method inside `DigestService`:

```python
    async def _congress_prompt_section(self, snapshot: WsbSnapshot) -> str:
        if not self.settings.congress_enabled:
            return ""
        try:
            congress_snapshot = await self.congress_collector.get_or_fetch()
            new_trades = self.congress_collector.read_new_trades()
        except Exception:
            logger.exception("Congress enrichment failed; continuing without congress section")
            return ""

        analyzer = CongressAnalyzer()
        signals = analyzer.top_signals(
            congress_snapshot.trades,
            new_trades=new_trades,
            lookback_days=self.settings.congress_lookback_days,
            min_score=self.settings.congress_min_signal_score,
        )
        evidence_by_ticker = {item.ticker: item for item in snapshot.ticker_evidence}
        signals = analyzer.apply_convergence_scores(signals, snapshot.trending_tickers, evidence_by_ticker)
        reddit_tickers = [item.ticker for item in snapshot.trending_tickers[:10]]
        overlap = sorted(analyzer.overlap_tickers(signals, reddit_tickers))
        lines = [analyzer.format_signal_line(signal, overlap=signal.ticker in overlap) for signal in signals]
        congress_section = "\n".join(lines) if lines else "No notable congressional activity this period."
        return render_congress_digest_prompt(
            congress_section_markdown=congress_section,
            lookback_days=self.settings.congress_lookback_days,
            overlap_tickers=overlap,
        )
```

In `generate()`, after the existing `prompt = render_digest_prompt(` call closes, append:

```python
        congress_prompt = await self._congress_prompt_section(snapshot)
        if congress_prompt:
            prompt = f"{prompt}\n\n{congress_prompt}"
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
uv run pytest tests/test_digest.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finage/digest.py tests/test_digest.py
git commit -m "feat: enrich digest with congress signals"
```

---

### Task 7: Analysis Service `/senate` And Live Badges

**Files:**
- Modify: `src/finage/analysis.py`
- Modify: `tests/test_analysis.py`

- [ ] **Step 1: Add failing analysis tests**

Append to `tests/test_analysis.py`:

```python
from datetime import UTC, datetime

from finage.congress import normalize_congress_trade
from finage.models import CongressSnapshot, CongressTrade


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
        make_settings(tmp_path),
        collector=FakeCollector(make_snapshot()),
        congress_collector=FakeCongressCollector([make_congress_trade("TSLA")]),
    )

    result = await service.live()

    assert "**TSLA** #1 🏛️" in result
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/test_analysis.py -v
```

Expected: FAIL because `MomentumAnalysisService.__init__` does not accept `congress_collector`.

- [ ] **Step 3: Add congress analysis helpers**

Update imports in `src/finage/analysis.py`:

```python
from finage.congress import CongressAnalyzer, CongressCollector
from finage.models import CongressTrade, PostEvidence, TickerEvidence, TrendingTicker, WsbSnapshot
from finage.prompting import render_congress_ticker_prompt, render_ticker_why_prompt
```

Add formatting helper:

```python
def _format_congress_trade_line(trade: CongressTrade) -> str:
    return (
        f"- {trade.disclosure_date.isoformat()}: {trade.representative} "
        f"{trade.transaction_type.lower()} {trade.amount} of {trade.ticker} "
        f"({trade.chamber}; transaction {trade.transaction_date.isoformat()}; "
        f"{trade.disclosure_lag_days}d disclosure lag)"
    )
```

Modify `format_live_brief` signature:

```python
def format_live_brief(snapshot: WsbSnapshot, *, limit: int = 5, congress_badges: set[str] | None = None) -> str:
```

Inside the loop before appending ticker lines:

```python
        badge = " 🏛️" if congress_badges and trending.ticker in congress_badges else ""
```

Change both ticker line starts:

```python
f"- **{trending.ticker}** #{trending.rank}{badge}: "
```

and:

```python
f"- **{evidence.ticker}** #{trending.rank}{badge}: "
```

Modify `MomentumAnalysisService.__init__`:

```python
        congress_collector=None,
```

```python
        self.congress_collector = congress_collector or CongressCollector(settings)
```

Add helper:

```python
    async def _congress_badges_for_snapshot(self, snapshot: WsbSnapshot) -> set[str]:
        if not self.settings.congress_enabled:
            return set()
        try:
            congress_snapshot = await self.congress_collector.get_or_fetch()
            new_trades = self.congress_collector.read_new_trades()
        except Exception:
            logger.exception("Congress badge lookup failed")
            return set()
        analyzer = CongressAnalyzer()
        signals = analyzer.top_signals(
            congress_snapshot.trades,
            new_trades=new_trades,
            lookback_days=self.settings.congress_lookback_days,
            min_score=self.settings.congress_min_signal_score,
        )
        social = {item.ticker for item in snapshot.trending_tickers[:10]}
        return {signal.ticker for signal in signals if signal.ticker in social}
```

Update `live()`:

```python
        congress_badges = await self._congress_badges_for_snapshot(snapshot)
        return format_live_brief(snapshot, congress_badges=congress_badges)
```

Add method:

```python
    async def senate(self, symbol: str) -> str:
        ticker = normalize_ticker_symbol(symbol)
        logger.info("Starting senate scan for ticker=%s", ticker)
        try:
            congress_snapshot = await self.congress_collector.get_or_fetch()
        except Exception:
            logger.exception("Congress data unavailable for ticker=%s", ticker)
            return "Congressional data unavailable. FMP could not be reached and no local cache is available."

        trades = self.congress_collector.trades_for_ticker(congress_snapshot, ticker)
        fetch_trending = getattr(self.collector, "fetch_trending_tickers", None)
        trending = []
        if fetch_trending is not None:
            try:
                trending = await fetch_trending()
            except Exception:
                logger.exception("ApeWisdom rank lookup failed for senate ticker=%s", ticker)
        rank_by_ticker = {item.ticker: item.rank for item in trending}
        reddit_rank = f"#{rank_by_ticker[ticker]}" if ticker in rank_by_ticker else f"not in top {self.settings.wsb_ticker_limit}"

        if trades:
            trades_formatted = "\n".join(_format_congress_trade_line(trade) for trade in trades[:25])
            asset_description = trades[0].asset_description
        else:
            trades_formatted = "No congressional trades found in local history."
            asset_description = ticker

        prompt = render_congress_ticker_prompt(
            ticker=ticker,
            asset_description=asset_description,
            trades_formatted=trades_formatted,
            reddit_rank=reddit_rank,
            ticker_limit=self.settings.wsb_ticker_limit,
        )
        explanation = await self.llm_provider.generate(prompt)
        return f"**Congressional Activity: {ticker}**\n{explanation.strip()}"
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
uv run pytest tests/test_analysis.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finage/analysis.py tests/test_analysis.py
git commit -m "feat: analyze congress trades in momentum service"
```

---

### Task 8: Telegram `/senate` Command

**Files:**
- Modify: `src/finage/telegram_bot.py`
- Modify: `tests/test_telegram_bot.py`

- [ ] **Step 1: Add failing Telegram tests**

Append to `tests/test_telegram_bot.py`:

```python
@pytest.mark.asyncio
async def test_help_lists_senate_command() -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=999),
        effective_message=message,
    )
    context = SimpleNamespace(bot=FakeBot())

    await TelegramDigestBot(make_settings()).help(update, context)

    assert "/senate <stock>" in message.replies[0]


@pytest.mark.asyncio
async def test_senate_requires_one_symbol_argument() -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=999),
        effective_message=message,
    )
    context = SimpleNamespace(bot=FakeBot(), args=[])

    await TelegramDigestBot(make_settings()).senate(update, context)

    assert message.replies == ["Usage: /senate TSLA"]


@pytest.mark.asyncio
async def test_senate_sends_markdown_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeMomentumAnalysisService:
        def __init__(self, settings: Settings):
            self.settings = settings

        async def senate(self, symbol: str) -> str:
            assert symbol == "TSLA"
            return "**Congressional Activity: TSLA**\n- focused congress brief"

    monkeypatch.setattr("finage.telegram_bot.MomentumAnalysisService", FakeMomentumAnalysisService)
    message = FakeMessage()
    bot = FakeBot()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=999),
        effective_message=message,
    )
    context = SimpleNamespace(bot=bot, args=["tsla"])

    await TelegramDigestBot(make_settings()).senate(update, context)

    assert message.replies == ["Analyzing congressional trades for TSLA..."]
    assert bot.messages[0]["chat_id"] == 999
    assert "<b>Congressional Activity: TSLA</b>" in bot.messages[0]["text"]
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/test_telegram_bot.py -v
```

Expected: FAIL because `TelegramDigestBot` has no `senate` method.

- [ ] **Step 3: Register and implement `/senate`**

In `TelegramDigestBot.run()`, add:

```python
        application.add_handler(CommandHandler("senate", self.senate))
```

Update `start()` message to include:

```python
            "/senate TSLA for congressional trading analysis, or /health for bot status."
```

Update `help()` message to include:

```python
            "/senate <stock> - analyze Senate and House trading disclosures for one ticker.\n"
```

Add method to `TelegramDigestBot`:

```python
    async def senate(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return

        if len(context.args) != 1:
            await update.effective_message.reply_text("Usage: /senate TSLA")
            return

        try:
            symbol = normalize_ticker_symbol(context.args[0])
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc).replace("/ticker", "/senate"))
            return

        chat_id = update.effective_chat.id if update.effective_chat else None
        user_id = update.effective_user.id if update.effective_user else None
        logger.info("Received /senate request from user_id=%s chat_id=%s ticker=%s", user_id, chat_id, symbol)
        await update.effective_message.reply_text(f"Analyzing congressional trades for {symbol}...")
        try:
            brief = await MomentumAnalysisService(self.settings).senate(symbol)
            await send_markdown_text(context.bot, update.effective_chat.id, brief)
            logger.info("Completed /senate request for chat_id=%s ticker=%s", chat_id, symbol)
        except Exception:
            logger.exception("Failed to generate senate brief")
            await update.effective_message.reply_text("Senate analysis failed. Check the Pi logs.")
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
uv run pytest tests/test_telegram_bot.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finage/telegram_bot.py tests/test_telegram_bot.py
git commit -m "feat: add senate telegram command"
```

---

### Task 9: CLI Refresh And Environment Documentation

**Files:**
- Modify: `src/finage/cli.py`
- Modify: `.env.example`
- Modify: `tests/test_congress.py`

- [ ] **Step 1: Add failing CLI parser test**

Append to `tests/test_congress.py`:

```python
from finage.cli import build_parser


def test_cli_supports_congress_refresh_command() -> None:
    args = build_parser().parse_args(["congress", "refresh"])

    assert args.command == "congress"
    assert args.congress_command == "refresh"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/test_congress.py::test_cli_supports_congress_refresh_command -v
```

Expected: FAIL because the `congress` subcommand does not exist.

- [ ] **Step 3: Add CLI parser and runner**

Modify `src/finage/cli.py` imports:

```python
from finage.congress import CongressCollector
```

Add parser setup in `build_parser()` before `return parser`:

```python
    congress_parser = subparsers.add_parser("congress", help="Manage congressional trading data")
    congress_subparsers = congress_parser.add_subparsers(dest="congress_command", required=True)
    congress_subparsers.add_parser("refresh", help="Fetch and persist latest Senate and House trades")
```

Add async runner:

```python
async def _run_congress_command(settings: Settings, congress_command: str) -> int:
    if congress_command == "refresh":
        collector = CongressCollector(settings)
        snapshot = await collector.fetch(bootstrap=collector.get_cached() is None)
        print(
            "Congress refresh complete: "
            f"total_trades={snapshot.total_trades} "
            f"new_trades={snapshot.new_trades} "
            f"corrected_trades={snapshot.corrected_trades} "
            f"cache={collector.base_dir}"
        )
        return 0

    raise ValueError(f"Unsupported congress command: {congress_command}")
```

Add command handling in `main()`:

```python
    if args.command == "congress":
        return asyncio.run(_run_congress_command(settings, args.congress_command))
```

- [ ] **Step 4: Update `.env.example`**

Append to `.env.example`:

```dotenv

# Optional: FMP congressional trading data for daily digest enrichment and /senate.
FMP_API_KEY=
CONGRESS_LOOKBACK_DAYS=30
CONGRESS_BOOTSTRAP_DAYS=180
CONGRESS_MAX_PAGES_PER_REFRESH=4
CONGRESS_CACHE_TTL_HOURS=12
CONGRESS_MIN_SIGNAL_SCORE=2.0
```

- [ ] **Step 5: Run tests and verify pass**

Run:

```bash
uv run pytest tests/test_congress.py::test_cli_supports_congress_refresh_command -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/finage/cli.py .env.example tests/test_congress.py
git commit -m "feat: add congress refresh cli"
```

---

### Task 10: Final Verification

**Files:**
- No new files expected.
- Verify all files touched by the feature.

- [ ] **Step 1: Run all tests**

Run:

```bash
uv run pytest -v
```

Expected: PASS.

- [ ] **Step 2: Run lint diagnostics in Cursor**

Use Cursor `ReadLints` for:

```text
src/finage/congress.py
src/finage/models.py
src/finage/settings.py
src/finage/prompting.py
src/finage/digest.py
src/finage/analysis.py
src/finage/telegram_bot.py
src/finage/cli.py
```

Expected: no new diagnostics in edited files.

- [ ] **Step 3: Smoke-test CLI parser locally without network**

Run:

```bash
uv run finage congress refresh
```

Expected if `FMP_API_KEY` is missing: command exits with a clear `FMP_API_KEY is required for congress data` error. This is acceptable on a local machine without secrets. If `FMP_API_KEY` is configured, expected output starts with:

```text
Congress refresh complete:
```

- [ ] **Step 4: Review git diff**

Run:

```bash
git status --short
git diff --stat
```

Expected: only files listed in this plan are modified, and unrelated `brainstorm.md` remains unstaged unless the user explicitly asks to include it.

- [ ] **Step 5: Commit final verification fixes if any were needed**

If Task 10 required code changes:

```bash
git add <changed feature files>
git commit -m "fix: polish congress trades integration"
```

If Task 10 required no changes, do not create an empty commit.

---

## Self-Review Notes

Spec coverage:
- FMP endpoints, bootstrap pagination, daily pagination, TTL fallback: Tasks 4 and 9.
- Local history, latest fetch metadata, new trades, ticker files: Task 3.
- Models, settings, and env docs: Tasks 1 and 9.
- Deterministic scoring and convergence: Task 2.
- Digest append-only congressional section: Task 6.
- `/senate <TICKER>` no-data and trade-history analysis: Tasks 7 and 8.
- Live congress badge: Task 7.
- Test plan coverage: Tasks 1 through 10.

Type consistency:
- `CongressTrade`, `CongressSnapshot`, `CongressFetchMetadata`, and `CongressSignal` use the field names from the spec.
- `CongressCollector.get_or_fetch()`, `fetch()`, `get_cached()`, `read_new_trades()`, and `trades_for_ticker()` are used consistently across digest, analysis, and CLI tasks.
- `CongressAnalyzer.top_signals()`, `apply_convergence_scores()`, `overlap_tickers()`, and `format_signal_line()` are used consistently across digest and live enrichment.

Execution boundary:
- Each task has a failing test, implementation, verification command, and commit.
- The plan avoids adding a database, background worker, or new scheduler dependency. Daily execution is a CLI command intended for PM2/cron.
