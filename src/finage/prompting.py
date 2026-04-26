from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from finage.models import WsbSnapshot

DEFAULT_DIGEST_PROMPT = "wsb_digest.md"
EVIDENCE_PLACEHOLDER = "{evidence_json}"


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def build_digest_payload(snapshot: WsbSnapshot) -> dict:
    tickers = []
    for evidence in snapshot.ticker_evidence:
        posts = []
        for post in evidence.posts[:3]:
            posts.append(
                {
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

        tickers.append(
            {
                "ticker": evidence.ticker,
                "apewisdom_rank": evidence.trending.rank if evidence.trending else None,
                "apewisdom_mentions": evidence.trending.mentions if evidence.trending else 0,
                "apewisdom_upvotes": evidence.trending.upvotes if evidence.trending else 0,
                "posts": posts,
            }
        )

    return {
        "generated_at": snapshot.generated_at.isoformat(),
        "subreddit": snapshot.subreddit,
        "trending_tickers": [ticker.model_dump() for ticker in snapshot.trending_tickers],
        "ticker_evidence": tickers,
    }


def load_digest_prompt_template(prompt_template_path: Path | None = None) -> str:
    if prompt_template_path is not None:
        return prompt_template_path.read_text(encoding="utf-8")

    return resources.files("finage.prompts").joinpath(DEFAULT_DIGEST_PROMPT).read_text(encoding="utf-8")


def render_digest_prompt(snapshot: WsbSnapshot, *, prompt_template_path: Path | None = None) -> str:
    payload = build_digest_payload(snapshot)
    evidence_json = json.dumps(payload, indent=2, default=str)
    template = load_digest_prompt_template(prompt_template_path)
    if EVIDENCE_PLACEHOLDER not in template:
        raise ValueError(f"Digest prompt template must include {EVIDENCE_PLACEHOLDER}")
    return template.replace(EVIDENCE_PLACEHOLDER, evidence_json)
