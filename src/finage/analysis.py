from __future__ import annotations

import logging
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from finage.artifacts import ArtifactStore, LATEST_DIGEST_FILENAME, LATEST_SNAPSHOT_FILENAME
from finage.collector import WsbCollector
from finage.congress import CongressAnalyzer, CongressCollector
from finage.llm import LlmProvider, create_llm_provider
from finage.models import CongressTrade, PostEvidence, TickerEvidence, TrendingTicker, WsbSnapshot
from finage.prompting import render_congress_ticker_prompt, render_ticker_why_prompt
from finage.settings import Settings

logger = logging.getLogger(__name__)
TICKER_RE = re.compile(r"^\$?[A-Za-z]{1,5}$")


class Collector(Protocol):
    async def collect(self) -> WsbSnapshot:
        ...


@dataclass(frozen=True)
class HealthCheck:
    status: str
    name: str
    detail: str


def _format_int(value: int) -> str:
    return f"{value:,}"


def normalize_ticker_symbol(value: str) -> str:
    symbol = value.strip().upper()
    if symbol.startswith("$"):
        symbol = symbol[1:]

    if not symbol or not TICKER_RE.fullmatch(value.strip()):
        raise ValueError("Ticker must be 1-5 letters, for example `/ticker TSLA`.")

    return symbol


def _truncate(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _subreddit_summary(evidence: TickerEvidence) -> str:
    counts = Counter(post.subreddit for post in evidence.posts if post.subreddit)
    if not counts:
        return "no subreddit source"

    top_sources = [f"r/{name} x{count}" for name, count in counts.most_common(3)]
    return ", ".join(top_sources)


def _subreddit_count(evidence: TickerEvidence | None) -> int:
    if evidence is None:
        return 0
    return len({post.subreddit for post in evidence.posts if post.subreddit})


def _top_post_summary(evidence: TickerEvidence) -> str:
    if not evidence.posts:
        return "No qualifying posts found."

    post = evidence.posts[0]
    source = f"r/{post.subreddit}" if post.subreddit else "unknown subreddit"
    return f"{post.title} ({source}, {_format_int(post.score)} score, {_format_int(post.num_comments)} comments)"


def _top_trending_context(snapshot: WsbSnapshot, *, limit: int = 5) -> str:
    if not snapshot.trending_tickers:
        return "No current ApeWisdom tickers were available."

    tickers = ", ".join(f"{item.ticker} #{item.rank}" for item in snapshot.trending_tickers[:limit])
    return f"Current top tickers: {tickers}."


def _format_trending_context(trending: TrendingTicker | None) -> str:
    if trending is None:
        return "ApeWisdom rank: not in the current trend list."

    return (
        f"ApeWisdom rank: #{trending.rank}; "
        f"mentions: {_format_int(trending.mentions)}; upvotes: {_format_int(trending.upvotes)}."
    )


def _format_post_detail(post: PostEvidence) -> str:
    source = f"r/{post.subreddit}" if post.subreddit else "unknown subreddit"
    parts = [
        f"- {post.title} ({source}, {_format_int(post.score)} score, {_format_int(post.num_comments)} comments)",
        f"  {post.url}",
    ]

    if post.selftext:
        parts.append(f"  Summary: {_truncate(post.selftext, 220)}")

    for comment in post.top_comments[:2]:
        parts.append(f"  Comment ({_format_int(comment.score)}): {_truncate(comment.content, 180)}")

    if post.external_links:
        parts.append(f"  External links: {', '.join(post.external_links[:3])}")

    return "\n".join(parts)


def format_ticker_brief(snapshot: WsbSnapshot, symbol: str) -> str:
    """Build a focused Telegram-ready evidence card for a single ticker."""

    ticker = normalize_ticker_symbol(symbol)
    trending_by_ticker = {item.ticker: item for item in snapshot.trending_tickers}
    evidence_by_ticker = {item.ticker: item for item in snapshot.ticker_evidence}
    trending = trending_by_ticker.get(ticker)
    evidence = evidence_by_ticker.get(ticker)
    source_scope = ", ".join(f"r/{name}" for name in (snapshot.subreddits or [snapshot.subreddit]))

    lines = [
        f"**{ticker} Social Momentum**",
        f"Generated: {snapshot.generated_at:%Y-%m-%d %H:%M UTC}",
        f"Sources: ApeWisdom `{snapshot.subreddit}` trend list + {source_scope}",
        _format_trending_context(trending),
        "",
    ]

    if evidence is None:
        if trending is None:
            lines.extend(
                [
                    f"**Read:** {ticker} is not in the current ApeWisdom trend list, so Finage did not collect targeted Reddit evidence for it in this scan.",
                    _top_trending_context(snapshot),
                ]
            )
        else:
            lines.extend(
                [
                    f"**Read:** {ticker} is trending, but no qualifying Reddit posts passed the configured score/comment filters in this scan.",
                    _top_trending_context(snapshot),
                ]
            )
        lines.extend(["", "Not financial advice; this is social-media evidence, not live price action."])
        return "\n".join(lines)

    lines.extend(
        [
            f"Evidence score: {_format_int(evidence.evidence_score)}",
            f"Subreddit breadth: {_subreddit_summary(evidence)}",
            f"Qualifying posts: {len(evidence.posts)}",
            "",
            "**Top Evidence**",
        ]
    )

    for post in evidence.posts[:3]:
        lines.append(_format_post_detail(post))

    lines.extend(["", "Not financial advice; this is social-media evidence, not live price action."])
    return "\n".join(lines)


def _evidence_by_ticker(snapshot: WsbSnapshot) -> dict[str, TickerEvidence]:
    return {item.ticker: item for item in snapshot.ticker_evidence}


def _trending_by_ticker(snapshot: WsbSnapshot) -> dict[str, TrendingTicker]:
    return {item.ticker: item for item in snapshot.trending_tickers}


def _format_delta(value: int) -> str:
    if value > 0:
        return f"+{_format_int(value)}"
    return _format_int(value)


def _format_rank_delta(current_rank: int, previous_rank: int) -> str:
    delta = previous_rank - current_rank
    if delta > 0:
        return f"up {delta} spots"
    if delta < 0:
        return f"down {abs(delta)} spots"
    return "unchanged"


def _format_artifact_age(generated_at: datetime) -> str:
    now = datetime.now(UTC)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=UTC)
    age = now - generated_at
    total_minutes = max(0, int(age.total_seconds() // 60))
    if total_minutes < 60:
        return f"{total_minutes}m old"
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours}h {minutes}m old"


def format_health_report(checks: list[HealthCheck]) -> str:
    counts = Counter(check.status for check in checks)
    if counts.get("FAIL"):
        overall = "FAIL"
    elif counts.get("WARN"):
        overall = "WARN"
    else:
        overall = "OK"

    lines = [
        "**Finage Health**",
        f"Overall: {overall}",
        "",
    ]
    for check in checks:
        lines.append(f"- `{check.status}` **{check.name}**: {check.detail}")
    return "\n".join(lines)


def format_movers_brief(current: WsbSnapshot, previous: WsbSnapshot | None, *, limit: int = 5) -> str:
    """Build a read-only movement brief comparing a fresh scan to the latest saved baseline."""

    source_scope = ", ".join(f"r/{name}" for name in (current.subreddits or [current.subreddit]))
    lines = [
        "**Social Momentum Movers**",
        f"Current: {current.generated_at:%Y-%m-%d %H:%M UTC}",
        f"Sources: ApeWisdom `{current.subreddit}` trend list + {source_scope}",
        "",
    ]

    if previous is None:
        lines.extend(
            [
                "No saved baseline snapshot found. Run `/digest` or `finage digest send` first so `/movers` can compare a fresh scan against the latest persisted snapshot.",
                _top_trending_context(current),
                "",
                "Not financial advice; this is social-media evidence, not live price action.",
            ]
        )
        return "\n".join(lines)

    lines.append(f"Baseline: {previous.generated_at:%Y-%m-%d %H:%M UTC}")
    current_trending = _trending_by_ticker(current)
    previous_trending = _trending_by_ticker(previous)
    current_evidence = _evidence_by_ticker(current)
    previous_evidence = _evidence_by_ticker(previous)

    new_entrants = [
        item
        for item in current.trending_tickers
        if item.ticker not in previous_trending
    ][:limit]

    rank_changes = [
        (item, previous_trending[item.ticker])
        for item in current.trending_tickers
        if item.ticker in previous_trending and item.rank != previous_trending[item.ticker].rank
    ]
    rank_risers = sorted(
        [pair for pair in rank_changes if pair[0].rank < pair[1].rank],
        key=lambda pair: pair[1].rank - pair[0].rank,
        reverse=True,
    )[:limit]
    rank_fallers = sorted(
        [pair for pair in rank_changes if pair[0].rank > pair[1].rank],
        key=lambda pair: pair[0].rank - pair[1].rank,
        reverse=True,
    )[:limit]

    mention_spikes = sorted(
        [
            (item, item.mentions - previous_trending[item.ticker].mentions)
            for item in current.trending_tickers
            if item.ticker in previous_trending and item.mentions > previous_trending[item.ticker].mentions
        ],
        key=lambda pair: pair[1],
        reverse=True,
    )[:limit]

    upvote_spikes = sorted(
        [
            (item, item.upvotes - previous_trending[item.ticker].upvotes)
            for item in current.trending_tickers
            if item.ticker in previous_trending and item.upvotes > previous_trending[item.ticker].upvotes
        ],
        key=lambda pair: pair[1],
        reverse=True,
    )[:limit]

    evidence_leaders = sorted(current.ticker_evidence, key=lambda item: item.evidence_score, reverse=True)[:limit]
    breadth_gainers = sorted(
        [
            (
                evidence,
                _subreddit_count(evidence) - _subreddit_count(previous_evidence.get(evidence.ticker)),
            )
            for evidence in current.ticker_evidence
            if _subreddit_count(evidence) > _subreddit_count(previous_evidence.get(evidence.ticker))
        ],
        key=lambda pair: pair[1],
        reverse=True,
    )[:limit]

    lines.append("")
    lines.append("**New Entrants**")
    if new_entrants:
        for item in new_entrants:
            lines.append(
                f"- **{item.ticker}** entered at #{item.rank}: {_format_int(item.mentions)} mentions, {_format_int(item.upvotes)} upvotes."
            )
    else:
        lines.append("- None in the current top trend list.")

    lines.append("")
    lines.append("**Rank Risers**")
    if rank_risers:
        for current_item, previous_item in rank_risers:
            lines.append(
                f"- **{current_item.ticker}** #{previous_item.rank} -> #{current_item.rank} ({_format_rank_delta(current_item.rank, previous_item.rank)})."
            )
    else:
        lines.append("- No rank risers versus baseline.")

    lines.append("")
    lines.append("**Rank Fallers**")
    if rank_fallers:
        for current_item, previous_item in rank_fallers:
            lines.append(
                f"- **{current_item.ticker}** #{previous_item.rank} -> #{current_item.rank} ({_format_rank_delta(current_item.rank, previous_item.rank)})."
            )
    else:
        lines.append("- No rank fallers versus baseline.")

    lines.append("")
    lines.append("**Mention Spikes**")
    if mention_spikes:
        for item, delta in mention_spikes:
            lines.append(f"- **{item.ticker}** {_format_delta(delta)} mentions to {_format_int(item.mentions)} total.")
    else:
        lines.append("- No positive mention deltas versus baseline.")

    lines.append("")
    lines.append("**Upvote Spikes**")
    if upvote_spikes:
        for item, delta in upvote_spikes:
            lines.append(f"- **{item.ticker}** {_format_delta(delta)} upvotes to {_format_int(item.upvotes)} total.")
    else:
        lines.append("- No positive upvote deltas versus baseline.")

    lines.append("")
    lines.append("**Evidence Leaders**")
    if evidence_leaders:
        for evidence in evidence_leaders:
            previous_score = previous_evidence.get(evidence.ticker).evidence_score if evidence.ticker in previous_evidence else 0
            delta = evidence.evidence_score - previous_score
            lines.append(
                f"- **{evidence.ticker}** evidence score {_format_int(evidence.evidence_score)} ({_format_delta(delta)}), "
                f"{_subreddit_summary(evidence)}. Top thread: {_top_post_summary(evidence)}"
            )
    else:
        lines.append("- No qualifying Reddit evidence in the current scan.")

    if breadth_gainers:
        lines.append("")
        lines.append("**Broader Subreddit Coverage**")
        for evidence, delta in breadth_gainers:
            lines.append(
                f"- **{evidence.ticker}** gained {_format_delta(delta)} subreddit sources: {_subreddit_summary(evidence)}."
            )

    lines.extend(["", "Not financial advice; this is social-media evidence, not live price action."])
    return "\n".join(lines)


def _format_congress_trade_line(trade: CongressTrade) -> str:
    return (
        f"- {trade.disclosure_date.isoformat()}: {trade.representative} "
        f"{trade.transaction_type.lower()} {trade.amount} of {trade.ticker} "
        f"({trade.chamber}; transaction {trade.transaction_date.isoformat()}; "
        f"{trade.disclosure_lag_days}d disclosure lag)"
    )


def format_live_brief(snapshot: WsbSnapshot, *, limit: int = 5, congress_badges: set[str] | None = None) -> str:
    """Build a deterministic Telegram-ready brief for the latest social momentum scan."""

    source_scope = ", ".join(f"r/{name}" for name in (snapshot.subreddits or [snapshot.subreddit]))
    lines = [
        "**Live Social Momentum**",
        f"Generated: {snapshot.generated_at:%Y-%m-%d %H:%M UTC}",
        f"Sources: ApeWisdom `{snapshot.subreddit}` trend list + {source_scope}",
        "",
    ]

    if not snapshot.trending_tickers:
        lines.extend(
            [
                "No trending tickers came back from ApeWisdom.",
                "",
                "Not financial advice; this is social-media evidence, not live price action.",
            ]
        )
        return "\n".join(lines)

    evidence_by_ticker = {evidence.ticker: evidence for evidence in snapshot.ticker_evidence}
    ranked_tickers = snapshot.trending_tickers[:limit]
    lines.append("**Top Momentum Tickers**")

    for trending in ranked_tickers:
        evidence = evidence_by_ticker.get(trending.ticker)
        badge = " 🏛️" if congress_badges and trending.ticker in congress_badges else ""
        if evidence is None:
            lines.append(
                f"- **{trending.ticker}** #{trending.rank}{badge}: "
                f"{_format_int(trending.mentions)} mentions, {_format_int(trending.upvotes)} upvotes. "
                "No qualifying Reddit evidence found in the configured scan."
            )
            continue

        lines.append(
            f"- **{evidence.ticker}** #{trending.rank}{badge}: "
            f"{_format_int(trending.mentions)} mentions, {_format_int(trending.upvotes)} upvotes, "
            f"evidence score {_format_int(evidence.evidence_score)} across {_subreddit_summary(evidence)}. "
            f"Top thread: {_top_post_summary(evidence)}"
        )

    evidence_without_trend = [
        evidence
        for evidence in sorted(snapshot.ticker_evidence, key=lambda item: item.evidence_score, reverse=True)
        if evidence.trending is None
    ]
    if evidence_without_trend:
        lines.append("")
        lines.append("**Notable Evidence Outside Top Trend List**")
        for evidence in evidence_without_trend[:3]:
            lines.append(
                f"- **{evidence.ticker}**: evidence score {_format_int(evidence.evidence_score)} "
                f"across {_subreddit_summary(evidence)}. Top thread: {_top_post_summary(evidence)}"
            )

    lines.extend(
        [
            "",
            "Not financial advice; this is social-media evidence, not live price action.",
        ]
    )
    return "\n".join(lines)


class MomentumAnalysisService:
    def __init__(
        self,
        settings: Settings,
        *,
        collector: Collector | None = None,
        artifact_store: ArtifactStore | None = None,
        llm_provider: LlmProvider | None = None,
        congress_collector=None,
    ):
        self.settings = settings
        self.collector = collector or WsbCollector(settings)
        self.artifact_store = artifact_store or ArtifactStore(settings.data_dir)
        self.llm_provider = llm_provider or create_llm_provider(settings)
        self.congress_collector = congress_collector or CongressCollector(settings)

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

    async def live(self) -> str:
        logger.info("Starting live momentum scan")
        snapshot = await self.collector.collect()
        logger.info(
            "Live momentum scan complete: trending=%s evidence_tickers=%s",
            len(snapshot.trending_tickers),
            len(snapshot.ticker_evidence),
        )
        congress_badges = await self._congress_badges_for_snapshot(snapshot)
        return format_live_brief(snapshot, congress_badges=congress_badges)

    async def ticker(self, symbol: str) -> str:
        ticker = normalize_ticker_symbol(symbol)
        logger.info("Starting ticker momentum scan for ticker=%s", ticker)
        snapshot = await self.collector.collect()
        logger.info(
            "Ticker momentum scan complete: ticker=%s trending=%s evidence_tickers=%s",
            ticker,
            len(snapshot.trending_tickers),
            len(snapshot.ticker_evidence),
        )
        return format_ticker_brief(snapshot, ticker)

    async def movers(self) -> str:
        logger.info("Starting movers scan")
        previous = self.artifact_store.read_latest_snapshot()
        snapshot = await self.collector.collect()
        logger.info(
            "Movers scan complete: baseline_found=%s trending=%s evidence_tickers=%s",
            previous is not None,
            len(snapshot.trending_tickers),
            len(snapshot.ticker_evidence),
        )
        return format_movers_brief(snapshot, previous)

    async def why(self, symbol: str) -> str:
        ticker = normalize_ticker_symbol(symbol)
        logger.info("Starting why scan for ticker=%s", ticker)
        snapshot = await self.collector.collect()
        trending = _trending_by_ticker(snapshot).get(ticker)
        evidence = _evidence_by_ticker(snapshot).get(ticker)
        if evidence is None:
            logger.info("Skipping why LLM call for ticker=%s because no evidence was found", ticker)
            return format_ticker_brief(snapshot, ticker)

        prompt = render_ticker_why_prompt(snapshot, ticker, evidence, trending)
        logger.info("Rendered why prompt for ticker=%s chars=%s", ticker, len(prompt))
        explanation = await self.llm_provider.generate(prompt)
        return f"**Why {ticker}?**\n{explanation.strip()}"

    async def health(self) -> str:
        logger.info("Starting health check")
        checks: list[HealthCheck] = []

        checks.append(
            HealthCheck(
                "OK" if self.settings.telegram_allowed_ids else "FAIL",
                "Telegram allowlist",
                f"{len(self.settings.telegram_allowed_ids)} allowed IDs configured"
                if self.settings.telegram_allowed_ids
                else "TELEGRAM_ALLOWED_IDS is empty; bot will reject all users",
            )
        )
        checks.append(
            HealthCheck(
                "OK" if self.settings.telegram_default_chat_id is not None else "WARN",
                "Default chat",
                f"TELEGRAM_DEFAULT_CHAT_ID={self.settings.telegram_default_chat_id}"
                if self.settings.telegram_default_chat_id is not None
                else "Missing TELEGRAM_DEFAULT_CHAT_ID; scheduled sends will fail",
            )
        )
        checks.append(
            HealthCheck(
                "OK",
                "LLM",
                f"provider={self.settings.llm_provider}, model={self.settings.gemini_model}, key configured",
            )
        )
        checks.append(
            HealthCheck(
                "OK" if self.settings.wsb_subreddits else "FAIL",
                "Collection settings",
                (
                    f"ApeWisdom filter={self.settings.wsb_subreddit}; "
                    f"{len(self.settings.wsb_subreddits)} subreddits; "
                    f"post_limit={self.settings.wsb_post_limit}; ticker_limit={self.settings.wsb_ticker_limit}; "
                    f"min_score={self.settings.wsb_min_score}; min_comments={self.settings.wsb_min_comments}"
                ),
            )
        )

        data_dir = self.settings.data_dir
        if data_dir.exists():
            checks.append(
                HealthCheck(
                    "OK" if data_dir.is_dir() and os.access(data_dir, os.R_OK | os.W_OK) else "FAIL",
                    "Data directory",
                    f"{data_dir} exists"
                    if data_dir.is_dir() and os.access(data_dir, os.R_OK | os.W_OK)
                    else f"{data_dir} is not readable/writable",
                )
            )
        else:
            parent = data_dir.parent if data_dir.parent != data_dir else data_dir
            checks.append(
                HealthCheck(
                    "WARN" if parent.exists() and os.access(parent, os.W_OK) else "FAIL",
                    "Data directory",
                    f"{data_dir} does not exist yet; parent is writable"
                    if parent.exists() and os.access(parent, os.W_OK)
                    else f"{data_dir} does not exist and parent is not writable",
                )
            )

        snapshot_path = data_dir / LATEST_SNAPSHOT_FILENAME
        try:
            snapshot = self.artifact_store.read_latest_snapshot()
        except Exception as exc:
            checks.append(HealthCheck("FAIL", "Latest snapshot", f"{snapshot_path} is unreadable: {exc}"))
        else:
            checks.append(
                HealthCheck(
                    "OK" if snapshot is not None else "WARN",
                    "Latest snapshot",
                    f"{snapshot_path} generated {_format_artifact_age(snapshot.generated_at)}"
                    if snapshot is not None
                    else f"{snapshot_path} is missing; run /digest to create a baseline",
                )
            )

        digest_path = data_dir / LATEST_DIGEST_FILENAME
        try:
            digest = self.artifact_store.read_latest_digest()
        except Exception as exc:
            checks.append(HealthCheck("FAIL", "Latest digest", f"{digest_path} is unreadable: {exc}"))
        else:
            checks.append(
                HealthCheck(
                    "OK" if digest is not None else "WARN",
                    "Latest digest",
                    f"{digest_path} generated {_format_artifact_age(digest.generated_at)}"
                    if digest is not None
                    else f"{digest_path} is missing; scheduled digest has not produced an artifact yet",
                )
            )

        fetch_trending = getattr(self.collector, "fetch_trending_tickers", None)
        if fetch_trending is None:
            checks.append(HealthCheck("WARN", "ApeWisdom", "Trending check skipped for injected collector"))
        else:
            try:
                trending = await fetch_trending()
            except Exception as exc:
                checks.append(HealthCheck("FAIL", "ApeWisdom", f"Trending request failed: {exc}"))
            else:
                checks.append(
                    HealthCheck(
                        "OK" if trending else "WARN",
                        "ApeWisdom",
                        f"Fetched {len(trending)} trending tickers"
                        if trending
                        else "Request succeeded but returned no tickers",
                    )
                )

        logger.info("Health check complete with statuses=%s", dict(Counter(check.status for check in checks)))
        return format_health_report(checks)

    async def senate(self, symbol: str) -> str:
        ticker = normalize_ticker_symbol(symbol)
        logger.info("Starting senate scan for ticker=%s", ticker)
        try:
            congress_snapshot = await self.congress_collector.get_or_fetch()
        except Exception:
            logger.exception("Congress data unavailable for ticker=%s", ticker)
            return (
                "Congressional data unavailable. FMP could not be reached and no local cache is available."
            )

        trades = self.congress_collector.trades_for_ticker(congress_snapshot, ticker)
        fetch_trending = getattr(self.collector, "fetch_trending_tickers", None)
        trending: list[TrendingTicker] = []
        if fetch_trending is not None:
            try:
                trending = await fetch_trending()
            except Exception:
                logger.exception("ApeWisdom rank lookup failed for senate ticker=%s", ticker)
        rank_by_ticker = {item.ticker: item.rank for item in trending}
        reddit_rank = (
            f"#{rank_by_ticker[ticker]}"
            if ticker in rank_by_ticker
            else f"not in top {self.settings.wsb_ticker_limit}"
        )

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
