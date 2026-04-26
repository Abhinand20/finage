from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Iterable

import asyncpraw
import httpx

from finage.models import CommentEvidence, PostEvidence, TickerEvidence, TrendingTicker, WsbSnapshot
from finage.settings import Settings

logger = logging.getLogger(__name__)

APEWISDOM_URL = "https://apewisdom.io/api/v1.0/filter/{filter_name}"
EXCLUDED_LINK_DOMAINS = (
    "reddit.com",
    "redd.it",
    "imgur.com",
    "gfycat.com",
    "redgifs.com",
    "giphy.com",
    "imgflip.com",
    "youtu.be",
    "discord.gg",
)
EXCLUDED_FLAIRS = {"Meme", "Shitpost", "Gain", "Loss"}
URL_RE = re.compile(r"https?://[^\s)\]}\"']+", re.IGNORECASE)


def is_valid_external_link(url: str) -> bool:
    return not any(domain in url.lower() for domain in EXCLUDED_LINK_DOMAINS)


def extract_external_links(text: str) -> list[str]:
    if not text:
        return []
    return sorted({url for url in URL_RE.findall(text) if is_valid_external_link(url)})


def extract_ticker_mentions(text: str, candidate_tickers: Iterable[str]) -> list[str]:
    if not text:
        return []

    mentions: set[str] = set()
    for ticker in candidate_tickers:
        normalized = ticker.upper().strip()
        if not normalized:
            continue

        bare_pattern = rf"(?<![A-Z0-9]){re.escape(normalized)}(?![A-Z0-9])"
        cash_pattern = rf"(?<![A-Z0-9])\${re.escape(normalized)}(?![A-Z0-9])"
        if re.search(cash_pattern, text, re.IGNORECASE):
            mentions.add(normalized)
        elif len(normalized) > 1 and re.search(bare_pattern, text, re.IGNORECASE):
            mentions.add(normalized)

    return sorted(mentions)


class WsbCollector:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def collect(self) -> WsbSnapshot:
        trending = await self.fetch_trending_tickers()
        candidate_tickers = [item.ticker for item in trending]
        posts_by_ticker: dict[str, list[PostEvidence]] = defaultdict(list)

        if not candidate_tickers:
            logger.warning("No trending tickers found from ApeWisdom")
            return WsbSnapshot(subreddit=self.settings.wsb_subreddit, trending_tickers=[])

        reddit = self._reddit_client()
        try:
            subreddit = await reddit.subreddit(self.settings.wsb_subreddit)
            async for submission in subreddit.hot(limit=self.settings.wsb_post_limit):
                post = await self._post_evidence(submission, candidate_tickers)
                if not post:
                    continue

                for ticker in post.mentioned_tickers:
                    posts_by_ticker[ticker].append(post)
        finally:
            await reddit.close()

        trending_by_ticker = {item.ticker: item for item in trending}
        ticker_evidence = [
            TickerEvidence(
                ticker=ticker,
                trending=trending_by_ticker.get(ticker),
                posts=sorted(posts, key=lambda item: item.score + item.num_comments, reverse=True),
            )
            for ticker, posts in posts_by_ticker.items()
        ]
        ticker_evidence.sort(key=lambda item: (item.trending.rank if item.trending else 999, -item.evidence_score))

        return WsbSnapshot(
            subreddit=self.settings.wsb_subreddit,
            trending_tickers=trending,
            ticker_evidence=ticker_evidence,
        )

    async def fetch_trending_tickers(self) -> list[TrendingTicker]:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(APEWISDOM_URL.format(filter_name=self.settings.wsb_subreddit))
            response.raise_for_status()
            payload = response.json()

        results = payload.get("results", [])
        if not isinstance(results, list):
            return []

        ranked: list[TrendingTicker] = []
        for index, item in enumerate(results[: self.settings.wsb_ticker_limit], start=1):
            ticker = str(item.get("ticker", "")).upper().strip()
            if not ticker:
                continue

            ranked.append(
                TrendingTicker(
                    ticker=ticker,
                    rank=index,
                    mentions=int(item.get("mentions") or 0),
                    upvotes=int(item.get("upvotes") or 0),
                )
            )

        return ranked

    def _reddit_client(self) -> asyncpraw.Reddit:
        return asyncpraw.Reddit(
            client_id=self.settings.reddit_client_id,
            client_secret=self.settings.reddit_client_secret,
            user_agent=self.settings.reddit_user_agent,
        )

    async def _post_evidence(self, submission, candidate_tickers: list[str]) -> PostEvidence | None:
        flair = submission.link_flair_text or ""
        if flair in EXCLUDED_FLAIRS:
            return None
        if submission.score < self.settings.wsb_min_score:
            return None
        if submission.num_comments < self.settings.wsb_min_comments:
            return None

        content = f"{submission.title}\n{submission.selftext or ''}"
        post_mentions = set(extract_ticker_mentions(content, candidate_tickers))
        external_links = set(extract_external_links(submission.selftext or ""))

        if not getattr(submission, "is_self", True) and is_valid_external_link(submission.url):
            external_links.add(submission.url)

        comments = await self._top_comments(submission, candidate_tickers)
        for comment in comments:
            post_mentions.update(comment.mentioned_tickers)
            external_links.update(extract_external_links(comment.content))

        if not post_mentions:
            return None

        return PostEvidence(
            id=submission.id,
            url=f"https://www.reddit.com{submission.permalink}",
            title=submission.title,
            selftext=submission.selftext or "",
            score=submission.score,
            num_comments=submission.num_comments,
            upvote_ratio=getattr(submission, "upvote_ratio", None),
            link_flair_text=flair,
            created_utc=getattr(submission, "created_utc", None),
            mentioned_tickers=sorted(post_mentions),
            top_comments=comments,
            external_links=sorted(external_links),
        )

    async def _top_comments(self, submission, candidate_tickers: list[str]) -> list[CommentEvidence]:
        load = getattr(submission, "load", None)
        if load is not None:
            await load()

        comments_forest = getattr(submission, "comments", None)
        if comments_forest is None:
            logger.warning("Skipping comments for submission %s: comments were not loaded", submission.id)
            return []

        try:
            await comments_forest.replace_more(limit=0)
            comments = await comments_forest.list()
        except (TypeError, AttributeError) as exc:
            logger.warning("Skipping comments for submission %s: %s", submission.id, exc)
            return []

        top_comments = sorted(comments, key=lambda comment: getattr(comment, "score", 0), reverse=True)

        evidence: list[CommentEvidence] = []
        for comment in top_comments[: self.settings.wsb_top_comments]:
            body = getattr(comment, "body", "") or ""
            author = getattr(getattr(comment, "author", None), "name", None) or "[deleted]"
            evidence.append(
                CommentEvidence(
                    author=author,
                    content=body,
                    score=int(getattr(comment, "score", 0) or 0),
                    mentioned_tickers=extract_ticker_mentions(body, candidate_tickers),
                )
            )

        return evidence
