from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from finage.models import DigestResult, TickerEvidence, TrendingTicker, WsbSnapshot
from finage.web_search import WebSearchResponse

DEFAULT_DIGEST_PROMPT = "wsb_digest.md"
EVIDENCE_PLACEHOLDER = "{evidence_json}"
PREVIOUS_DIGEST_PLACEHOLDER = "{previous_digest_json}"
NO_PREVIOUS_DIGEST = {"available": False, "reason": "No previous digest artifact found."}


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _web_search_payload(response: WebSearchResponse) -> dict:
    return {
        "query": response.query,
        "results": [
            {
                "title": result.title,
                "url": result.url,
                "published_date": result.published_date,
                "author": result.author,
                "highlights": result.highlights,
                "text": _truncate(result.text, 1200) if result.text else None,
                "score": result.score,
            }
            for result in response.results[:5]
        ],
    }


def build_digest_payload(
    snapshot: WsbSnapshot,
    *,
    web_search_by_ticker: dict[str, WebSearchResponse] | None = None,
) -> dict:
    tickers = []
    for evidence in snapshot.ticker_evidence:
        posts = []
        for post in evidence.posts[:3]:
            posts.append(
                {
                    "subreddit": post.subreddit,
                    "title": post.title,
                    "url": post.url,
                    "score": post.score,
                    "comments": post.num_comments,
                    "flair": post.link_flair_text,
                    "mentioned_tickers": post.mentioned_tickers,
                    "summary_text": _truncate(post.selftext, 700),
                    "top_comments": [
                        {
                            "score": comment.score,
                            "mentioned_tickers": comment.mentioned_tickers,
                            "content": _truncate(comment.content, 350),
                        }
                        for comment in post.top_comments[:3]
                    ],
                    "external_links": post.external_links[:5],
                }
            )

        ticker_payload = {
            "ticker": evidence.ticker,
            "apewisdom_rank": evidence.trending.rank if evidence.trending else None,
            "apewisdom_mentions": evidence.trending.mentions if evidence.trending else 0,
            "apewisdom_upvotes": evidence.trending.upvotes if evidence.trending else 0,
            "posts": posts,
        }
        if web_search_by_ticker and evidence.ticker in web_search_by_ticker:
            ticker_payload["web_search"] = _web_search_payload(web_search_by_ticker[evidence.ticker])
        tickers.append(ticker_payload)

    return {
        "generated_at": snapshot.generated_at.isoformat(),
        "subreddit": snapshot.subreddit,
        "subreddits": snapshot.subreddits or [snapshot.subreddit],
        "trending_tickers": [ticker.model_dump() for ticker in snapshot.trending_tickers],
        "ticker_evidence": tickers,
    }


def build_ticker_why_payload(
    snapshot: WsbSnapshot,
    ticker: str,
    evidence: TickerEvidence,
    trending: TrendingTicker | None,
) -> dict:
    return {
        "generated_at": snapshot.generated_at.isoformat(),
        "ticker": ticker,
        "source_scope": {
            "apewisdom_filter": snapshot.subreddit,
            "subreddits": snapshot.subreddits or [snapshot.subreddit],
        },
        "apewisdom": trending.model_dump() if trending else None,
        "evidence_score": evidence.evidence_score,
        "subreddit_sources": sorted({post.subreddit for post in evidence.posts if post.subreddit}),
        "posts": [
            {
                "subreddit": post.subreddit,
                "title": post.title,
                "url": post.url,
                "score": post.score,
                "comments": post.num_comments,
                "flair": post.link_flair_text,
                "mentioned_tickers": post.mentioned_tickers,
                "summary_text": _truncate(post.selftext, 900),
                "top_comments": [
                    {
                        "score": comment.score,
                        "mentioned_tickers": comment.mentioned_tickers,
                        "content": _truncate(comment.content, 450),
                    }
                    for comment in post.top_comments[:5]
                ],
                "external_links": post.external_links[:5],
            }
            for post in evidence.posts[:5]
        ],
    }


def render_ticker_why_prompt(
    snapshot: WsbSnapshot,
    ticker: str,
    evidence: TickerEvidence,
    trending: TrendingTicker | None,
) -> str:
    payload = build_ticker_why_payload(snapshot, ticker, evidence, trending)
    evidence_json = json.dumps(payload, indent=2, default=str)
    return f"""Explain why {ticker} is showing up in the current social-momentum scan.

Goal:
Give a concise, decision-useful explanation based only on the supplied Reddit/ApeWisdom evidence.

Required output:
- **{ticker} read:** one sentence on the central reason it is moving socially.
- **Narrative:** recurring themes across posts/comments.
- **Sentiment:** Bullish, Bearish, Mixed, or Unclear, with confidence.
- **Catalysts:** concrete news, earnings, product, macro, rumor, or options-flow catalysts if present.
- **Evidence strength:** High, Medium, or Low. Consider repeat evidence, subreddit breadth, post quality, and comment quality.
- **Counterpoints / uncertainty:** what could make this noisy or wrong.
- **Watch next:** 1-3 practical signals to monitor next.

Rules:
- Do not fabricate missing data.
- Separate repeated evidence from one-off claims.
- Treat WSB/stock subreddit chatter as noisy and potentially biased.
- Do not present the output as financial advice.
- Keep it Telegram-friendly and concise.

Evidence JSON:
{evidence_json}
"""


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


def load_digest_prompt_template(
    prompt_template_path: Path | None = None,
    *,
    prompt_bundle: str = DEFAULT_DIGEST_PROMPT,
) -> str:
    if prompt_template_path is not None:
        return prompt_template_path.read_text(encoding="utf-8")

    return resources.files("finage.prompts").joinpath(prompt_bundle).read_text(encoding="utf-8")


def build_previous_digest_payload(previous_digest: DigestResult | None) -> dict:
    if previous_digest is None:
        return NO_PREVIOUS_DIGEST

    return {
        "available": True,
        "generated_at": previous_digest.generated_at.isoformat(),
        "provider": previous_digest.provider,
        "model": previous_digest.model,
        "digest": previous_digest.digest,
    }


def render_digest_prompt(
    snapshot: WsbSnapshot,
    *,
    previous_digest: DigestResult | None = None,
    web_search_by_ticker: dict[str, WebSearchResponse] | None = None,
    prompt_template_path: Path | None = None,
    prompt_bundle: str = DEFAULT_DIGEST_PROMPT,
) -> str:
    payload = build_digest_payload(snapshot, web_search_by_ticker=web_search_by_ticker)
    evidence_json = json.dumps(payload, indent=2, default=str)
    previous_digest_json = json.dumps(build_previous_digest_payload(previous_digest), indent=2, default=str)
    template = load_digest_prompt_template(prompt_template_path, prompt_bundle=prompt_bundle)
    if EVIDENCE_PLACEHOLDER not in template:
        raise ValueError(f"Digest prompt template must include {EVIDENCE_PLACEHOLDER}")

    prompt = template.replace(EVIDENCE_PLACEHOLDER, evidence_json)
    if PREVIOUS_DIGEST_PLACEHOLDER in prompt:
        return prompt.replace(PREVIOUS_DIGEST_PLACEHOLDER, previous_digest_json)

    return f"{prompt}\n\nPrevious Digest JSON:\n{previous_digest_json}\n"
