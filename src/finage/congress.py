from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Callable, Literal

import httpx

from finage.models import (
    CongressFetchMetadata,
    CongressSignal,
    CongressSnapshot,
    CongressTrade,
    TickerEvidence,
    TrendingTicker,
)
from finage.settings import Settings

logger = logging.getLogger(__name__)

AMOUNT_RE = re.compile(r"\$?([0-9,]+)")
SENATE_ENDPOINT = "https://financialmodelingprep.com/stable/senate-latest"
HOUSE_ENDPOINT = "https://financialmodelingprep.com/stable/house-latest"
FMP_PAGE_LIMIT = 25


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
            buy_score = sum(
                self._trade_score(trade) for trade in ticker_trades if trade.transaction_type == "Bought"
            )
            sell_score = sum(
                self._trade_score(trade) for trade in ticker_trades if trade.transaction_type == "Sold"
            )
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
            key=lambda item: item.convergence_score if item.convergence_score is not None else item.total_score,
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
            labels.append("⚡ CONVERGENCE")

        direction = f"{signal.buy_count} buys"
        if signal.sell_count:
            direction = f"{direction}, {signal.sell_count} sales"
        politician_text = ", ".join(signal.politicians[:3])
        label_text = f" · {' · '.join(labels)}" if labels else ""
        return (
            f"- **{signal.ticker}** — {direction} · est. ${signal.total_bought_value_midpoint:,} bought · "
            f"largest {signal.largest_amount} · {politician_text}{label_text}"
        )


class CongressCollector:
    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ):
        self.settings = settings
        self.base_dir = settings.data_dir / "congress"
        self.by_ticker_dir = self.base_dir / "by_ticker"
        self.client_factory = client_factory
        self.now_provider = now_provider or (lambda: datetime.now(UTC))

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
            else:
                existing_by_id[trade.trade_id] = existing.model_copy(update={"last_seen_at": fetched_at})

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

    def _client(self) -> httpx.AsyncClient:
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
            logger.exception("Congress fetch failed")
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
                endpoint=SENATE_ENDPOINT,
                fetched_at=fetched_at,
                bootstrap=bootstrap,
                last_successful_fetch_at=last_successful_fetch_at,
            )
            house_trades, house_pages = await self._fetch_chamber(
                client,
                chamber="House",
                endpoint=HOUSE_ENDPOINT,
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
        client: httpx.AsyncClient,
        *,
        chamber: Literal["Senate", "House"],
        endpoint: str,
        fetched_at: datetime,
        bootstrap: bool,
        last_successful_fetch_at: datetime | None,
    ) -> tuple[list[CongressTrade], int]:
        trades: list[CongressTrade] = []
        max_pages = self.settings.congress_max_pages_per_refresh

        if bootstrap:
            cutoff_date = fetched_at.date() - timedelta(days=self.settings.congress_bootstrap_days)
        elif last_successful_fetch_at is not None:
            cutoff_date = last_successful_fetch_at.date() - timedelta(days=1)
        else:
            cutoff_date = fetched_at.date() - timedelta(days=self.settings.congress_lookback_days)

        pages_fetched = 0
        for page in range(max_pages):
            response = await client.get(
                endpoint,
                params={
                    "page": page,
                    "limit": FMP_PAGE_LIMIT,
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
            if not page_trades:
                break
            oldest_disclosure = min(trade.disclosure_date for trade in page_trades)
            if oldest_disclosure < cutoff_date:
                break

        return trades, pages_fetched
