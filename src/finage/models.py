from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal

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
    subreddit: str = ""
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
    subreddits: list[str] = Field(default_factory=list)
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


class WhaleHolding(BaseModel):
    ticker: str
    cusip: str | None = None
    shares: int
    value_usd: int
    weight: float = 0.0


class WhaleChange(BaseModel):
    status: Literal["NEW", "INCREASED", "DECREASED", "CLOSED", "UNCHANGED"]
    ticker: str
    shares_delta: int
    shares_delta_pct: float | None = None
    value_delta_usd: int
    prior_shares: int
    prior_value_usd: int
    current_shares: int
    current_value_usd: int


class WhaleFundSnapshot(BaseModel):
    slug: str
    fund_name: str
    manager: str
    cik: str
    report_period: date
    filing_accession: str | None = None
    total_holdings: int = 0
    total_value_usd: int = 0
    holdings: list[WhaleHolding] = Field(default_factory=list)
    changes: list[WhaleChange] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WhaleSignal(BaseModel):
    ticker: str
    total_score: float
    convergence_score: float | None = None
    fund_count: int
    new_count: int
    increased_count: int
    decreased_count: int
    closed_count: int
    total_value_usd: int
    largest_position_fund: str
    largest_position_value_usd: int
    funds: list[str] = Field(default_factory=list)
    has_social_overlap: bool = False
    has_congress_overlap: bool = False
    labels: list[str] = Field(default_factory=list)


class WhaleSnapshot(BaseModel):
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    funds: list[WhaleFundSnapshot] = Field(default_factory=list)
    signals: list[WhaleSignal] = Field(default_factory=list)


class WhaleRefreshChanges(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    previous_fetched_at: datetime | None = None
    current_fetched_at: datetime
    new_filing_funds: list[str] = Field(default_factory=list)
    new_signal_tickers: list[str] = Field(default_factory=list)
    changed_signal_tickers: list[str] = Field(default_factory=list)
    dropped_signal_tickers: list[str] = Field(default_factory=list)
