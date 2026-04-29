from pathlib import Path

import pytest

from finage.digest import DigestService, build_digest_payload, build_digest_prompt
from finage.models import DigestResult, PostEvidence, TickerEvidence, TrendingTicker, WsbSnapshot
from finage.prompting import render_digest_prompt
from finage.settings import Settings
from finage.web_search import WebSearchResponse, WebSearchResult


class FakeCollector:
    def __init__(self, snapshot: WsbSnapshot):
        self.snapshot = snapshot

    async def collect(self) -> WsbSnapshot:
        return self.snapshot


class FakeLlm:
    name = "fake"
    model = "fake-model"

    def __init__(self) -> None:
        self.last_prompt = ""

    async def generate(self, prompt: str) -> str:
        self.last_prompt = prompt
        assert "Evidence JSON" in prompt
        return "WSB Momentum\n- TSLA: test digest.\nNot financial advice; WSB data is noisy."


class FakeWebSearchProvider:
    name = "fake-search"

    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.queries: list[str] = []

    async def search(self, query: str, options):
        self.queries.append(query)
        if self.should_fail:
            raise RuntimeError("search failed")
        return WebSearchResponse(
            query=query,
            provider=self.name,
            results=[
                WebSearchResult(
                    title="Tesla web catalyst",
                    url="https://example.com/tesla-web",
                    highlights=["Tesla web context"],
                )
            ],
        )


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        reddit_client_id="reddit-id",
        reddit_client_secret="reddit-secret",
        telegram_bot_token="telegram-token",
        telegram_allowed_ids=[123],
        telegram_default_chat_id=123,
        gemini_api_key="gemini-key",
        data_dir=tmp_path,
    )


def make_snapshot() -> WsbSnapshot:
    return WsbSnapshot(
        subreddit="wallstreetbets",
        subreddits=["wallstreetbets", "stocks"],
        trending_tickers=[TrendingTicker(ticker="TSLA", rank=1, mentions=10, upvotes=50)],
        ticker_evidence=[
            TickerEvidence(
                ticker="TSLA",
                trending=TrendingTicker(ticker="TSLA", rank=1, mentions=10, upvotes=50),
                posts=[
                    PostEvidence(
                        id="abc",
                        subreddit="stocks",
                        url="https://www.reddit.com/r/wallstreetbets/comments/abc",
                        title="TSLA catalyst thread",
                        score=500,
                        num_comments=100,
                        mentioned_tickers=["TSLA"],
                    )
                ],
            )
        ],
    )


def test_build_digest_payload_keeps_ticker_evidence() -> None:
    payload = build_digest_payload(make_snapshot())

    assert payload["ticker_evidence"][0]["ticker"] == "TSLA"
    assert payload["subreddits"] == ["wallstreetbets", "stocks"]
    assert payload["ticker_evidence"][0]["posts"][0]["subreddit"] == "stocks"
    assert payload["ticker_evidence"][0]["posts"][0]["title"] == "TSLA catalyst thread"


def test_build_digest_payload_includes_web_search_by_ticker() -> None:
    search_response = WebSearchResponse(
        query="TSLA stock latest news earnings analyst catalyst",
        provider="exa",
        request_id="request-123",
        results=[
            WebSearchResult(
                title="Tesla earnings preview",
                url="https://example.com/tesla",
                published_date="2026-04-28",
                author="Reporter",
                highlights=["Tesla earnings are due this week."],
                score=0.9,
            )
        ],
    )

    payload = build_digest_payload(make_snapshot(), web_search_by_ticker={"TSLA": search_response})

    web_search = payload["ticker_evidence"][0]["web_search"]
    assert web_search["query"] == "TSLA stock latest news earnings analyst catalyst"
    assert web_search["provider"] == "exa"
    assert web_search["results"][0]["title"] == "Tesla earnings preview"
    assert web_search["results"][0]["url"] == "https://example.com/tesla"


def test_build_digest_prompt_includes_required_caveat() -> None:
    prompt = build_digest_prompt(make_snapshot())

    assert "Evidence JSON" in prompt
    assert "TSLA catalyst thread" in prompt


def test_default_digest_prompt_describes_web_search_evidence() -> None:
    prompt = build_digest_prompt(make_snapshot())

    assert "Web search evidence may show recent news" in prompt
    assert "Use web search to ground catalysts" in prompt
    assert "This is market intelligence, not financial advice." in prompt
    assert "A brief \"watchlist read\"" in prompt


def test_render_digest_prompt_supports_custom_template(tmp_path: Path) -> None:
    prompt_path = tmp_path / "custom_prompt.md"
    prompt_path.write_text("Custom prompt\n{evidence_json}", encoding="utf-8")

    prompt = render_digest_prompt(make_snapshot(), prompt_template_path=prompt_path)

    assert prompt.startswith("Custom prompt")
    assert "TSLA catalyst thread" in prompt
    assert "Previous Digest JSON" in prompt


def test_render_digest_prompt_includes_previous_digest() -> None:
    previous = DigestResult(provider="gemini", model="gemini-test", digest="Yesterday: TSLA persisted.")

    prompt = render_digest_prompt(make_snapshot(), previous_digest=previous)

    assert "Previous Digest JSON" in prompt
    assert "Yesterday: TSLA persisted." in prompt


def test_render_digest_prompt_includes_web_search_context() -> None:
    search_response = WebSearchResponse(
        query="TSLA stock latest news earnings analyst catalyst",
        provider="exa",
        results=[
            WebSearchResult(
                title="Tesla catalyst",
                url="https://example.com/catalyst",
                highlights=["Tesla announced a catalyst."],
            )
        ],
    )

    prompt = render_digest_prompt(
        make_snapshot(),
        web_search_by_ticker={"TSLA": search_response},
    )

    assert '"web_search"' in prompt
    assert "Tesla catalyst" in prompt
    assert "https://example.com/catalyst" in prompt


@pytest.mark.asyncio
async def test_digest_service_writes_latest_artifacts(tmp_path: Path) -> None:
    llm = FakeLlm()
    service = DigestService(
        make_settings(tmp_path),
        collector=FakeCollector(make_snapshot()),
        llm_provider=llm,
    )

    result = await service.generate()

    assert "TSLA" in result.digest
    assert (tmp_path / "latest_wsb_snapshot.json").exists()
    assert (tmp_path / "latest_digest.json").exists()
    assert "No previous digest artifact found" in llm.last_prompt


@pytest.mark.asyncio
async def test_digest_service_includes_latest_digest_in_next_prompt(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    previous = DigestResult(provider="gemini", model="gemini-test", digest="Yesterday: AMD led the digest.")
    (tmp_path / "latest_digest.json").write_text(previous.model_dump_json(), encoding="utf-8")
    llm = FakeLlm()

    service = DigestService(settings, collector=FakeCollector(make_snapshot()), llm_provider=llm)

    await service.generate()

    assert "Yesterday: AMD led the digest." in llm.last_prompt


@pytest.mark.asyncio
async def test_digest_service_enriches_prompt_with_web_search(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.digest_web_search_enabled = True
    llm = FakeLlm()
    web_search = FakeWebSearchProvider()
    service = DigestService(
        settings,
        collector=FakeCollector(make_snapshot()),
        llm_provider=llm,
        web_search_provider=web_search,
    )

    await service.generate()

    assert web_search.queries == ["TSLA stock latest news earnings analyst catalyst"]
    assert "Tesla web catalyst" in llm.last_prompt
    assert "https://example.com/tesla-web" in llm.last_prompt


@pytest.mark.asyncio
async def test_digest_service_continues_when_web_search_fails(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.digest_web_search_enabled = True
    llm = FakeLlm()
    service = DigestService(
        settings,
        collector=FakeCollector(make_snapshot()),
        llm_provider=llm,
        web_search_provider=FakeWebSearchProvider(should_fail=True),
    )

    result = await service.generate()

    assert "TSLA" in result.digest
    assert "TSLA catalyst thread" in llm.last_prompt
    assert "web_search" not in llm.last_prompt
