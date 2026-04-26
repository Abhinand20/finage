from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class TrendingTicker(BaseModel):
    ticker: str
    rank: int
    mentions: int = 0
    upvotes: int = 0


class CommentEvidence(BaseModel):
    author: str
    content: str
    score: int
    mentioned_tickers: list[str] = Field(default_factory=list)


class PostEvidence(BaseModel):
    id: str
    url: str
    title: str
    selftext: str = ""
    score: int
    num_comments: int
    upvote_ratio: float | None = None
    link_flair_text: str = ""
    created_utc: float | None = None
    mentioned_tickers: list[str] = Field(default_factory=list)
    top_comments: list[CommentEvidence] = Field(default_factory=list)
    external_links: list[str] = Field(default_factory=list)


class TickerEvidence(BaseModel):
    ticker: str
    trending: TrendingTicker | None = None
    posts: list[PostEvidence] = Field(default_factory=list)

    @property
    def evidence_score(self) -> int:
        return sum(post.score + post.num_comments for post in self.posts)


class WsbSnapshot(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    subreddit: str
    trending_tickers: list[TrendingTicker] = Field(default_factory=list)
    ticker_evidence: list[TickerEvidence] = Field(default_factory=list)


class DigestResult(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provider: str
    model: str
    digest: str
    snapshot_path: str | None = None


class TelegramMessage(BaseModel):
    text: str
    parse_mode: str | None = None
    disable_web_page_preview: bool = True
