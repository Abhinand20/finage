from __future__ import annotations

import logging
from collections import Counter
from typing import Protocol

from finage.collector import WsbCollector
from finage.models import TickerEvidence, WsbSnapshot
from finage.settings import Settings

logger = logging.getLogger(__name__)


class Collector(Protocol):
    async def collect(self) -> WsbSnapshot:
        ...


def _format_int(value: int) -> str:
    return f"{value:,}"


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
