from __future__ import annotations

import asyncio
import json
import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from finage.models import (
    CongressSignal,
    TrendingTicker,
    WhaleChange,
    WhaleFundSnapshot,
    WhaleHolding,
    WhaleRefreshChanges,
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
        if isinstance(value, float) and math.isnan(value):
            return 0
        return int(value)
    if str(value).lower() in {"nan", "none", "<na>"}:
        return 0
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


def compute_whale_refresh_changes(
    previous: WhaleSnapshot | None,
    current: WhaleSnapshot,
    *,
    generated_at: datetime | None = None,
) -> WhaleRefreshChanges:
    if previous is None:
        return WhaleRefreshChanges(
            generated_at=generated_at or current.fetched_at,
            previous_fetched_at=None,
            current_fetched_at=current.fetched_at,
            new_filing_funds=[fund.fund_name for fund in current.funds],
            new_signal_tickers=[signal.ticker for signal in current.signals],
        )

    previous_funds = {fund.slug: fund for fund in previous.funds}
    new_filing_funds: list[str] = []
    for fund in current.funds:
        old = previous_funds.get(fund.slug)
        if old is None or old.filing_accession != fund.filing_accession or old.report_period != fund.report_period:
            new_filing_funds.append(fund.fund_name)

    previous_signals = {signal.ticker: signal for signal in previous.signals}
    current_signals = {signal.ticker: signal for signal in current.signals}
    new_signal_tickers = sorted(set(current_signals) - set(previous_signals))
    dropped_signal_tickers = sorted(set(previous_signals) - set(current_signals))
    changed_signal_tickers = sorted(
        ticker
        for ticker in set(current_signals) & set(previous_signals)
        if current_signals[ticker].labels != previous_signals[ticker].labels
        or current_signals[ticker].fund_count != previous_signals[ticker].fund_count
        or current_signals[ticker].total_score != previous_signals[ticker].total_score
    )

    return WhaleRefreshChanges(
        generated_at=generated_at or current.fetched_at,
        previous_fetched_at=previous.fetched_at,
        current_fetched_at=current.fetched_at,
        new_filing_funds=sorted(new_filing_funds),
        new_signal_tickers=new_signal_tickers,
        changed_signal_tickers=changed_signal_tickers,
        dropped_signal_tickers=dropped_signal_tickers,
    )


def format_whale_changes(changes: WhaleRefreshChanges | None) -> str:
    if changes is None:
        return "No previous whale refresh comparison is available."

    lines: list[str] = []
    if changes.new_filing_funds:
        lines.append(f"New or updated filings: {', '.join(changes.new_filing_funds[:10])}.")
    else:
        lines.append("No new fund filings since the previous successful whale refresh.")

    detail_lines: list[str] = []
    if changes.new_signal_tickers:
        detail_lines.append(f"New signals: {', '.join(changes.new_signal_tickers[:15])}.")
    if changes.changed_signal_tickers:
        detail_lines.append(f"Changed signals: {', '.join(changes.changed_signal_tickers[:15])}.")
    if changes.dropped_signal_tickers:
        detail_lines.append(f"Dropped signals: {', '.join(changes.dropped_signal_tickers[:15])}.")
    if not detail_lines:
        detail_lines.append("No signal set changes since the previous successful whale refresh.")

    return "\n".join(lines + detail_lines)


def format_whale_filing_updates(snapshot: WhaleSnapshot, changes: WhaleRefreshChanges | None) -> str:
    updated_names = set(changes.new_filing_funds) if changes else set()
    updated_funds = [fund for fund in snapshot.funds if fund.fund_name in updated_names]
    if updated_funds:
        lines = [
            f"- {fund.fund_name}: report {fund.report_period.isoformat()}"
            + (f", accession {fund.filing_accession}" if fund.filing_accession else "")
            for fund in sorted(updated_funds, key=lambda item: (item.report_period, item.fund_name), reverse=True)
        ]
        return "\n".join(lines)
    if updated_names:
        return "\n".join(f"- {name}: newly observed filing" for name in sorted(updated_names))

    latest = sorted(snapshot.funds, key=lambda item: (item.report_period, item.fund_name), reverse=True)[:5]
    if not latest:
        return "No whale filings are available in the current cache."
    return "No new fund filings since the previous successful whale refresh. Most recent cached filings:\n" + "\n".join(
        f"- {fund.fund_name}: report {fund.report_period.isoformat()}" for fund in latest
    )


def _frame_records(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    if hasattr(frame, "to_dict"):
        return list(frame.to_dict("records"))
    return list(frame)


def _get_attr(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default)


def _holdings_rows(filing: Any) -> list[dict[str, Any]]:
    if hasattr(filing, "holdings_view"):
        return _frame_records(filing.holdings_view())
    if hasattr(filing, "holdings_data"):
        return _frame_records(filing.holdings_data())
    holdings = _get_attr(filing, "holdings", None)
    return _frame_records(holdings)


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

    @property
    def latest_changes_path(self) -> Path:
        return self.base_dir / "latest_changes.json"

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

    def read_latest_changes(self) -> WhaleRefreshChanges | None:
        if not self.latest_changes_path.exists():
            return None
        return WhaleRefreshChanges.model_validate_json(self.latest_changes_path.read_text(encoding="utf-8"))

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

        if not funds:
            raise RuntimeError("No whale filings were fetched; check EDGAR_IDENTITY, network access, and edgartools API compatibility.")

        analyzer = WhaleAnalyzer()
        signals = analyzer.top_signals(funds, min_score=self.settings.whale_min_signal_score)
        snapshot = WhaleSnapshot(fetched_at=fetched_at, funds=funds, signals=signals)
        self.persist(snapshot)
        return snapshot

    async def _fetch_fund(self, edgar: Any, fund: WhaleFund, *, fetched_at: datetime) -> WhaleFundSnapshot:
        def load() -> Any:
            return edgar.Company(fund.cik).get_filings(form="13F-HR")[0].obj()

        filing = await asyncio.to_thread(load)
        total_value_usd = _value_to_usd(_get_attr(filing, "total_value", 0))
        holdings = [
            normalize_whale_holding(row, total_value_usd=total_value_usd)
            for row in _holdings_rows(filing)
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
        previous = self.get_cached()
        changes = compute_whale_refresh_changes(previous, snapshot, generated_at=self.now_provider())
        self._write_json(self.latest_path, snapshot.model_dump_json(indent=2))
        self._write_json(
            self.signals_path,
            json.dumps([signal.model_dump(mode="json") for signal in snapshot.signals], indent=2),
        )
        self._write_json(self.latest_changes_path, changes.model_dump_json(indent=2))
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
