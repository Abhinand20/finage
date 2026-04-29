import pytest

from finage.collector import WsbCollector, extract_external_links, extract_ticker_mentions
from finage.models import TrendingTicker
from finage.settings import DEFAULT_STOCK_SUBREDDITS, Settings


class FakeApeWisdomResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _patch_httpx_client(monkeypatch: pytest.MonkeyPatch, handler) -> list[str]:
    """handler(url: str) -> FakeApeWisdomResponse | BaseException to raise from get()"""
    calls: list[str] = []

    def client_factory(**kwargs):
        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, url: str):
                calls.append(url)
                out = handler(url)
                if isinstance(out, BaseException):
                    raise out
                return out

        return _Client()

    monkeypatch.setattr("finage.collector.httpx.AsyncClient", client_factory)
    return calls


def test_extract_ticker_mentions_prefers_candidate_tickers() -> None:
    candidates = ["TSLA", "NVDA", "A"]
    text = "WSB is watching $TSLA and nvda, but a normal article should not match ticker A."

    assert extract_ticker_mentions(text, candidates) == ["NVDA", "TSLA"]


def test_extract_ticker_mentions_allows_cash_prefixed_single_letter_tickers() -> None:
    assert extract_ticker_mentions("The post mentions $A calls.", ["A"]) == ["A"]


def test_extract_external_links_filters_reddit_media() -> None:
    links = extract_external_links(
        "See https://example.com/catalyst and https://reddit.com/r/wallstreetbets/comments/abc"
    )

    assert links == ["https://example.com/catalyst"]


def make_settings() -> Settings:
    return Settings(
        reddit_client_id="reddit-id",
        reddit_client_secret="reddit-secret",
        telegram_bot_token="telegram-token",
        telegram_allowed_ids=[123],
        telegram_default_chat_id=123,
        gemini_api_key="gemini-key",
    )


def test_settings_defaults_to_stock_subreddit_config() -> None:
    settings = make_settings()

    assert settings.wsb_subreddits == DEFAULT_STOCK_SUBREDDITS
    assert settings.wsb_subreddit == "wallstreetbets"


@pytest.mark.asyncio
async def test_fetch_trending_tickers_requests_each_configured_subreddit(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(url: str) -> FakeApeWisdomResponse:
        return FakeApeWisdomResponse({"results": []})

    calls = _patch_httpx_client(monkeypatch, handler)
    settings = make_settings()
    settings.wsb_subreddits = ["stocks", "options", "wallstreetbets"]

    trending = await WsbCollector(settings).fetch_trending_tickers()

    assert trending == []
    assert len(calls) == 3
    assert {c.split("/")[-1] for c in calls} == {"stocks", "options", "wallstreetbets"}


@pytest.mark.asyncio
async def test_fetch_trending_tickers_merges_duplicates_across_subreddits(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(url: str) -> FakeApeWisdomResponse:
        if "stocks" in url:
            return FakeApeWisdomResponse({"results": [{"ticker": "TSLA", "mentions": 10, "upvotes": 5}]})
        if "options" in url:
            return FakeApeWisdomResponse({"results": [{"ticker": "TSLA", "mentions": 3, "upvotes": 2}]})
        return FakeApeWisdomResponse({"results": []})

    calls = _patch_httpx_client(monkeypatch, handler)
    settings = make_settings()
    settings.wsb_subreddits = ["stocks", "options"]
    settings.wsb_ticker_limit = 10

    trending = await WsbCollector(settings).fetch_trending_tickers()

    assert len(calls) == 2
    assert len(trending) == 1
    assert trending[0].ticker == "TSLA"
    assert trending[0].rank == 1
    assert trending[0].mentions == 13
    assert trending[0].upvotes == 7


@pytest.mark.asyncio
async def test_fetch_trending_tickers_skips_failed_filter_and_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(url: str) -> FakeApeWisdomResponse | RuntimeError:
        if "broken_filter" in url:
            return RuntimeError("apewisdom down")
        if "stocks" in url:
            return FakeApeWisdomResponse({"results": [{"ticker": "NVDA", "mentions": 5, "upvotes": 1}]})
        return FakeApeWisdomResponse({"results": []})

    calls = _patch_httpx_client(monkeypatch, handler)
    settings = make_settings()
    settings.wsb_subreddits = ["broken_filter", "stocks"]

    trending = await WsbCollector(settings).fetch_trending_tickers()

    assert len(calls) == 2
    assert trending == [TrendingTicker(ticker="NVDA", rank=1, mentions=5, upvotes=1)]


@pytest.mark.asyncio
async def test_fetch_trending_tickers_ranks_by_mentions_upvotes_then_ticker(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(url: str) -> FakeApeWisdomResponse:
        return FakeApeWisdomResponse(
            {
                "results": [
                    {"ticker": "ZZZZ", "mentions": 5, "upvotes": 100},
                    {"ticker": "AAAA", "mentions": 10, "upvotes": 0},
                    {"ticker": "BBBB", "mentions": 10, "upvotes": 5},
                ]
            }
        )

    _patch_httpx_client(monkeypatch, handler)
    settings = make_settings()
    settings.wsb_subreddits = ["wallstreetbets"]
    settings.wsb_ticker_limit = 10

    trending = await WsbCollector(settings).fetch_trending_tickers()

    assert [t.ticker for t in trending] == ["BBBB", "AAAA", "ZZZZ"]
    assert [t.rank for t in trending] == [1, 2, 3]


class FakeSubmissionWithoutComments:
    id = "abc"
    comments = None

    async def load(self) -> None:
        self.comments = None


@pytest.mark.asyncio
async def test_top_comments_handles_unloaded_none_comment_forest() -> None:
    collector = WsbCollector(make_settings())

    comments = await collector._top_comments(FakeSubmissionWithoutComments(), ["TSLA"])

    assert comments == []


class FakeCommentForest:
    async def replace_more(self, limit: int) -> None:
        return None

    async def list(self) -> list:
        return []


class FakeSubmission:
    def __init__(self, *, submission_id: str, title: str):
        self.id = submission_id
        self.title = title
        self.selftext = ""
        self.score = 500
        self.num_comments = 100
        self.link_flair_text = "Discussion"
        self.is_self = True
        self.url = ""
        self.permalink = f"/r/test/comments/{submission_id}"
        self.comments = FakeCommentForest()


class FakeSubreddit:
    def __init__(self, submissions: list[FakeSubmission]):
        self.submissions = submissions

    async def hot(self, limit: int):
        for submission in self.submissions[:limit]:
            yield submission


class FakeReddit:
    def __init__(self, submissions_by_subreddit: dict[str, list[FakeSubmission]]):
        self.submissions_by_subreddit = submissions_by_subreddit
        self.requested_subreddits: list[str] = []
        self.closed = False

    async def subreddit(self, name: str) -> FakeSubreddit:
        self.requested_subreddits.append(name)
        return FakeSubreddit(self.submissions_by_subreddit[name])

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_collect_scans_configured_subreddits_and_tracks_post_source() -> None:
    settings = make_settings()
    settings.wsb_subreddits = ["stocks", "options"]
    fake_reddit = FakeReddit(
        {
            "stocks": [FakeSubmission(submission_id="stocks-post", title="TSLA catalyst thread")],
            "options": [FakeSubmission(submission_id="options-post", title="$TSLA calls thread")],
        }
    )
    collector = WsbCollector(settings)

    async def fake_trending() -> list[TrendingTicker]:
        return [TrendingTicker(ticker="TSLA", rank=1, mentions=10, upvotes=50)]

    collector.fetch_trending_tickers = fake_trending
    collector._reddit_client = lambda: fake_reddit

    snapshot = await collector.collect()

    assert snapshot.subreddits == ["stocks", "options"]
    assert fake_reddit.requested_subreddits == ["stocks", "options"]
    assert fake_reddit.closed
    assert len(snapshot.ticker_evidence) == 1
    posts = snapshot.ticker_evidence[0].posts
    assert {post.subreddit for post in posts} == {"stocks", "options"}
