from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Protocol

from finage.collector import WsbCollector
from finage.models import PostEvidence, TickerEvidence, TrendingTicker, WsbSnapshot
from finage.settings import Settings

logger = logging.getLogger(__name__)
TICKER_RE = re.compile(r"^\$?[A-Za-z]{1,5}$")


class Collector(Protocol):
    async def collect(self) -> WsbSnapshot:
        ...


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


def format_live_brief(snapshot: WsbSnapshot, *, limit: int = 5) -> str:
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
        if evidence is None:
            lines.append(
                f"- **{trending.ticker}** #{trending.rank}: "
                f"{_format_int(trending.mentions)} mentions, {_format_int(trending.upvotes)} upvotes. "
                "No qualifying Reddit evidence found in the configured scan."
            )
            continue

        lines.append(
            f"- **{evidence.ticker}** #{trending.rank}: "
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
    ):
        self.settings = settings
        self.collector = collector or WsbCollector(settings)

    async def live(self) -> str:
        logger.info("Starting live momentum scan")
        snapshot = await self.collector.collect()
        logger.info(
            "Live momentum scan complete: trending=%s evidence_tickers=%s",
            len(snapshot.trending_tickers),
            len(snapshot.ticker_evidence),
        )
        return format_live_brief(snapshot)

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
