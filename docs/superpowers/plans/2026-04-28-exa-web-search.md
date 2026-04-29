# Exa Web Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reusable Exa-backed web search support and use it to enrich digest prompts with recent web context for relevant tickers.

**Architecture:** Add a generic `finage.web_search` provider boundary with normalized Pydantic models, optional Exa configuration in `Settings`, prompt payload plumbing for per-ticker web results, and non-blocking digest enrichment. Keep `WsbSnapshot` as Reddit/social evidence only; assemble web search context only for LLM prompt input.

**Tech Stack:** Python 3.11, Pydantic v2, pytest, pytest-asyncio, Exa Python SDK (`exa-py`), existing `uv` project tooling.

---

## File Structure

- Create `src/finage/web_search.py`: generic web search models, provider protocol, Exa provider, factory, and normalization helpers.
- Modify `src/finage/settings.py`: optional Exa/search environment configuration.
- Modify `.env.example`: document Exa/search environment variables.
- Modify `pyproject.toml`: add `exa-py` dependency.
- Modify `src/finage/prompting.py`: accept optional per-ticker web search payloads and include them in `Evidence JSON`.
- Modify `src/finage/digest.py`: create and use optional web search provider, search relevant tickers, handle failures without failing digest generation.
- Modify `src/finage/prompts/wsb_digest.md`: teach Gemini how to use Reddit evidence and web search evidence differently.
- Add `tests/test_web_search.py`: unit tests for search settings, provider factory, content options, and result normalization.
- Modify `tests/test_digest.py`: tests for prompt payload enrichment and digest behavior when search succeeds or fails.

---

### Task 1: Add Optional Search Configuration

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/finage/settings.py`
- Modify: `.env.example`
- Test: `tests/test_web_search.py`

- [ ] **Step 1: Write failing tests for settings defaults and env parsing**

Create `tests/test_web_search.py` with these tests:

```python
from finage.settings import Settings


def make_settings(**overrides) -> Settings:
    values = {
        "reddit_client_id": "reddit-id",
        "reddit_client_secret": "reddit-secret",
        "telegram_bot_token": "telegram-token",
        "telegram_allowed_ids": [123],
        "telegram_default_chat_id": 123,
        "gemini_api_key": "gemini-key",
    }
    values.update(overrides)
    return Settings(**values)


def test_web_search_is_disabled_without_exa_key() -> None:
    settings = make_settings()

    assert settings.exa_api_key is None
    assert settings.web_search_provider is None
    assert not settings.digest_web_search_enabled
    assert settings.web_search_num_results == 3
    assert settings.web_search_content_mode == "highlights"
    assert settings.web_search_timeout_seconds == 15


def test_web_search_defaults_to_exa_when_api_key_is_present() -> None:
    settings = make_settings(exa_api_key="exa-key")

    assert settings.web_search_provider == "exa"
    assert settings.digest_web_search_enabled


def test_from_env_reads_optional_web_search_config(monkeypatch) -> None:
    env = {
        "REDDIT_CLIENT_ID": "reddit-id",
        "REDDIT_CLIENT_SECRET": "reddit-secret",
        "TELEGRAM_BOT_TOKEN": "telegram-token",
        "TELEGRAM_ALLOWED_IDS": "123",
        "TELEGRAM_DEFAULT_CHAT_ID": "123",
        "LLM_PROVIDER": "gemini",
        "GEMINI_API_KEY": "gemini-key",
        "EXA_API_KEY": "exa-key",
        "WEB_SEARCH_NUM_RESULTS": "5",
        "WEB_SEARCH_CONTENT_MODE": "text",
        "WEB_SEARCH_TIMEOUT_SECONDS": "9",
        "DIGEST_WEB_SEARCH_ENABLED": "false",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    settings = Settings.from_env()

    assert settings.exa_api_key == "exa-key"
    assert settings.web_search_provider == "exa"
    assert settings.web_search_num_results == 5
    assert settings.web_search_content_mode == "text"
    assert settings.web_search_timeout_seconds == 9
    assert not settings.digest_web_search_enabled
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run:

```bash
uv run pytest tests/test_web_search.py -v
```

Expected: FAIL because `Settings` does not have `exa_api_key`, `web_search_provider`, `digest_web_search_enabled`, `web_search_num_results`, `web_search_content_mode`, or `web_search_timeout_seconds`.

- [ ] **Step 3: Add the Exa SDK dependency**

Run:

```bash
uv add exa-py
```

Expected: `pyproject.toml` and `uv.lock` update with the latest available `exa-py` dependency.

- [ ] **Step 4: Add settings fields and env parsing**

Modify `src/finage/settings.py`:

```python
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field
```

Add helpers below `_int_env`:

```python
def _optional_str(raw: str | None) -> str | None:
    if raw is None or raw.strip() == "":
        return None
    return raw.strip()


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
```

Add fields to `Settings` after Gemini fields:

```python
    exa_api_key: str | None = None
    web_search_provider: Literal["exa"] | None = None
    web_search_num_results: int = 3
    web_search_content_mode: Literal["highlights", "text", "none"] = "highlights"
    web_search_timeout_seconds: int = 15
    digest_web_search_enabled: bool = False
```

Add a model post-init method inside `Settings`:

```python
    def model_post_init(self, __context: object) -> None:
        if self.exa_api_key and self.web_search_provider is None:
            self.web_search_provider = "exa"
        if (
            self.exa_api_key
            and self.web_search_provider == "exa"
            and "digest_web_search_enabled" not in self.model_fields_set
        ):
            self.digest_web_search_enabled = True
```

Add these arguments in `Settings.from_env()` after `gemini_model`:

```python
            exa_api_key=_optional_str(os.getenv("EXA_API_KEY")),
            web_search_provider=_optional_str(os.getenv("WEB_SEARCH_PROVIDER")),
            web_search_num_results=_int_env("WEB_SEARCH_NUM_RESULTS", 3),
            web_search_content_mode=os.getenv("WEB_SEARCH_CONTENT_MODE", "highlights"),
            web_search_timeout_seconds=_int_env("WEB_SEARCH_TIMEOUT_SECONDS", 15),
            digest_web_search_enabled=_bool_env(
                "DIGEST_WEB_SEARCH_ENABLED",
                bool(_optional_str(os.getenv("EXA_API_KEY"))),
            ),
```

- [ ] **Step 5: Update `.env.example`**

Add this block after Gemini settings:

```dotenv
# Optional Exa web search enrichment for LLM prompts.
EXA_API_KEY=
WEB_SEARCH_PROVIDER=
WEB_SEARCH_NUM_RESULTS=3
WEB_SEARCH_CONTENT_MODE=highlights
WEB_SEARCH_TIMEOUT_SECONDS=15
DIGEST_WEB_SEARCH_ENABLED=
```

- [ ] **Step 6: Run tests for settings**

Run:

```bash
uv run pytest tests/test_web_search.py -v
```

Expected: PASS for the three settings tests.

- [ ] **Step 7: Commit Task 1**

Run:

```bash
git add pyproject.toml uv.lock src/finage/settings.py .env.example tests/test_web_search.py
git commit -m "feat: configure optional web search"
```

---

### Task 2: Add Generic Web Search Provider

**Files:**
- Create: `src/finage/web_search.py`
- Modify: `tests/test_web_search.py`

- [ ] **Step 1: Add failing tests for provider factory, content options, and normalization**

Append to `tests/test_web_search.py`:

```python
from types import SimpleNamespace

import pytest

from finage.web_search import (
    ExaWebSearchProvider,
    WebSearchOptions,
    _contents_for_mode,
    _normalize_exa_result,
    create_web_search_provider,
)


def test_create_web_search_provider_returns_none_when_disabled() -> None:
    settings = make_settings(exa_api_key="exa-key", digest_web_search_enabled=False)

    assert create_web_search_provider(settings) is None


def test_create_web_search_provider_returns_exa_provider() -> None:
    settings = make_settings(exa_api_key="exa-key", digest_web_search_enabled=True)

    provider = create_web_search_provider(settings)

    assert isinstance(provider, ExaWebSearchProvider)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("highlights", {"highlights": True}),
        ("text", {"text": {"maxCharacters": 2000}}),
        ("none", False),
    ],
)
def test_contents_for_mode(mode: str, expected: dict | bool) -> None:
    assert _contents_for_mode(mode) == expected


def test_normalize_exa_result_handles_optional_fields() -> None:
    raw = SimpleNamespace(
        title="Tesla news",
        url="https://example.com/tesla",
        published_date="2026-04-28",
        author=None,
        highlights=["earnings catalyst"],
        text="Long article text",
        score=0.91,
    )

    result = _normalize_exa_result(raw)

    assert result.title == "Tesla news"
    assert result.url == "https://example.com/tesla"
    assert result.published_date == "2026-04-28"
    assert result.author is None
    assert result.highlights == ["earnings catalyst"]
    assert result.text == "Long article text"
    assert result.score == 0.91


@pytest.mark.asyncio
async def test_exa_provider_search_normalizes_results() -> None:
    class FakeExaClient:
        async def search(self, query: str, **kwargs):
            assert query == "TSLA stock latest news"
            assert kwargs["num_results"] == 2
            assert kwargs["type"] == "auto"
            assert kwargs["contents"] == {"highlights": True}
            return SimpleNamespace(
                request_id="request-123",
                results=[
                    SimpleNamespace(
                        title="Tesla catalyst",
                        url="https://example.com/catalyst",
                        published_date="2026-04-28",
                        author="Reporter",
                        highlights=["Tesla catalyst highlight"],
                        text=None,
                        score=0.8,
                    )
                ],
            )

    provider = ExaWebSearchProvider(api_key="exa-key", client=FakeExaClient())

    response = await provider.search(
        "TSLA stock latest news",
        WebSearchOptions(num_results=2, content_mode="highlights"),
    )

    assert response.query == "TSLA stock latest news"
    assert response.provider == "exa"
    assert response.request_id == "request-123"
    assert response.results[0].title == "Tesla catalyst"
```

- [ ] **Step 2: Run the provider tests to verify they fail**

Run:

```bash
uv run pytest tests/test_web_search.py -v
```

Expected: FAIL because `finage.web_search` does not exist.

- [ ] **Step 3: Implement `src/finage/web_search.py`**

Create `src/finage/web_search.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from finage.settings import Settings

logger = logging.getLogger(__name__)

ContentMode = Literal["highlights", "text", "none"]


class WebSearchOptions(BaseModel):
    num_results: int = Field(default=3, ge=1, le=10)
    content_mode: ContentMode = "highlights"
    search_type: str = "auto"
    text_max_characters: int = 2000
    include_domains: list[str] | None = None
    exclude_domains: list[str] | None = None


class WebSearchResult(BaseModel):
    title: str
    url: str
    published_date: str | None = None
    author: str | None = None
    highlights: list[str] = Field(default_factory=list)
    text: str | None = None
    score: float | None = None


class WebSearchResponse(BaseModel):
    query: str
    provider: str
    results: list[WebSearchResult] = Field(default_factory=list)
    request_id: str | None = None


class WebSearchProvider(Protocol):
    name: str

    async def search(self, query: str, options: WebSearchOptions) -> WebSearchResponse:
        ...


def _contents_for_mode(content_mode: ContentMode, *, text_max_characters: int = 2000) -> dict | bool:
    if content_mode == "highlights":
        return {"highlights": True}
    if content_mode == "text":
        return {"text": {"maxCharacters": text_max_characters}}
    return False


def _get_attr(value: object, snake_name: str, camel_name: str | None = None):
    if hasattr(value, snake_name):
        return getattr(value, snake_name)
    if camel_name and hasattr(value, camel_name):
        return getattr(value, camel_name)
    return None


def _normalize_exa_result(value: object) -> WebSearchResult:
    title = _get_attr(value, "title") or ""
    url = _get_attr(value, "url") or _get_attr(value, "id") or ""
    published_date = _get_attr(value, "published_date", "publishedDate")
    highlights = _get_attr(value, "highlights") or []

    return WebSearchResult(
        title=str(title),
        url=str(url),
        published_date=str(published_date) if published_date else None,
        author=_get_attr(value, "author"),
        highlights=[str(highlight) for highlight in highlights],
        text=_get_attr(value, "text"),
        score=_get_attr(value, "score"),
    )


@dataclass
class ExaWebSearchProvider:
    api_key: str
    timeout_seconds: int = 15
    client: object | None = None
    name: str = "exa"

    def _client(self):
        if self.client is not None:
            return self.client

        from exa_py import AsyncExa

        self.client = AsyncExa(api_key=self.api_key)
        return self.client

    async def search(self, query: str, options: WebSearchOptions) -> WebSearchResponse:
        logger.info(
            "Calling Exa search query=%r num_results=%s content_mode=%s",
            query,
            options.num_results,
            options.content_mode,
        )
        response = await self._client().search(
            query,
            num_results=options.num_results,
            type=options.search_type,
            contents=_contents_for_mode(
                options.content_mode,
                text_max_characters=options.text_max_characters,
            ),
            include_domains=options.include_domains,
            exclude_domains=options.exclude_domains,
        )

        results = [_normalize_exa_result(result) for result in getattr(response, "results", [])]
        return WebSearchResponse(
            query=query,
            provider=self.name,
            results=results,
            request_id=_get_attr(response, "request_id", "requestId"),
        )


def create_web_search_provider(settings: Settings) -> WebSearchProvider | None:
    if not settings.digest_web_search_enabled:
        return None
    if settings.web_search_provider is None:
        return None
    if settings.web_search_provider == "exa":
        if not settings.exa_api_key:
            return None
        return ExaWebSearchProvider(
            api_key=settings.exa_api_key,
            timeout_seconds=settings.web_search_timeout_seconds,
        )

    raise ValueError(f"Unsupported web search provider: {settings.web_search_provider}")
```

- [ ] **Step 4: Run provider tests**

Run:

```bash
uv run pytest tests/test_web_search.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

Run:

```bash
git add src/finage/web_search.py tests/test_web_search.py
git commit -m "feat: add Exa web search provider"
```

---

### Task 3: Plumb Search Results Into Prompt Payloads

**Files:**
- Modify: `src/finage/prompting.py`
- Modify: `tests/test_digest.py`

- [ ] **Step 1: Add failing prompt payload tests**

Modify imports in `tests/test_digest.py`:

```python
from finage.web_search import WebSearchResponse, WebSearchResult
```

Add this test after `test_build_digest_payload_keeps_ticker_evidence`:

```python
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
```

Add this test after `test_render_digest_prompt_includes_previous_digest`:

```python
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
```

- [ ] **Step 2: Run digest tests to verify they fail**

Run:

```bash
uv run pytest tests/test_digest.py -v
```

Expected: FAIL because `build_digest_payload()` and `render_digest_prompt()` do not accept `web_search_by_ticker`.

- [ ] **Step 3: Update prompt payload construction**

Modify `src/finage/prompting.py` imports:

```python
from finage.models import DigestResult, WsbSnapshot
from finage.web_search import WebSearchResponse
```

Add a helper above `build_digest_payload()`:

```python
def _web_search_payload(response: WebSearchResponse) -> dict:
    return {
        "query": response.query,
        "provider": response.provider,
        "request_id": response.request_id,
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
```

Change the `build_digest_payload()` signature:

```python
def build_digest_payload(
    snapshot: WsbSnapshot,
    *,
    web_search_by_ticker: dict[str, WebSearchResponse] | None = None,
) -> dict:
```

Inside the ticker loop, replace the current `tickers.append(...)` block with:

```python
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
```

Change the `render_digest_prompt()` signature:

```python
def render_digest_prompt(
    snapshot: WsbSnapshot,
    *,
    previous_digest: DigestResult | None = None,
    web_search_by_ticker: dict[str, WebSearchResponse] | None = None,
    prompt_template_path: Path | None = None,
) -> str:
```

Change the payload line:

```python
    payload = build_digest_payload(snapshot, web_search_by_ticker=web_search_by_ticker)
```

- [ ] **Step 4: Run prompt payload tests**

Run:

```bash
uv run pytest tests/test_digest.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

Run:

```bash
git add src/finage/prompting.py tests/test_digest.py
git commit -m "feat: include web search in digest prompts"
```

---

### Task 4: Add Non-Blocking Digest Enrichment

**Files:**
- Modify: `src/finage/digest.py`
- Modify: `tests/test_digest.py`

- [ ] **Step 1: Add fake web search classes and failing digest service tests**

Add this fake class near `FakeLlm` in `tests/test_digest.py`:

```python
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
```

Add these tests after `test_digest_service_includes_latest_digest_in_next_prompt`:

```python
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
    assert "Tesla catalyst thread" in llm.last_prompt
    assert "web_search" not in llm.last_prompt
```

- [ ] **Step 2: Run digest tests to verify they fail**

Run:

```bash
uv run pytest tests/test_digest.py -v
```

Expected: FAIL because `DigestService` does not accept `web_search_provider`.

- [ ] **Step 3: Implement digest search orchestration**

Modify `src/finage/digest.py` imports:

```python
from finage.web_search import WebSearchOptions, WebSearchProvider, WebSearchResponse, create_web_search_provider
```

Add this helper below `build_digest_prompt()`:

```python
def build_ticker_web_search_query(ticker: str) -> str:
    return f"{ticker} stock latest news earnings analyst catalyst"
```

Change `DigestService.__init__()` signature:

```python
        web_search_provider: WebSearchProvider | None = None,
```

Add this assignment in `__init__()`:

```python
        self.web_search_provider = web_search_provider or create_web_search_provider(settings)
```

Add this private method to `DigestService`:

```python
    async def _web_search_by_ticker(self, snapshot: WsbSnapshot) -> dict[str, WebSearchResponse]:
        if not self.web_search_provider or not self.settings.digest_web_search_enabled:
            return {}

        options = WebSearchOptions(
            num_results=self.settings.web_search_num_results,
            content_mode=self.settings.web_search_content_mode,
        )
        results: dict[str, WebSearchResponse] = {}
        for evidence in snapshot.ticker_evidence:
            query = build_ticker_web_search_query(evidence.ticker)
            try:
                response = await self.web_search_provider.search(query, options)
            except Exception:
                logger.exception("Web search failed for ticker=%s query=%r", evidence.ticker, query)
                continue

            if response.results:
                results[evidence.ticker] = response

        return results
```

In `generate()`, insert this after writing the snapshot and before `render_digest_prompt()`:

```python
        web_search_by_ticker = await self._web_search_by_ticker(snapshot)
        logger.info("Web search enrichment complete for tickers=%s", len(web_search_by_ticker))
```

Pass the enrichment into `render_digest_prompt()`:

```python
        prompt = render_digest_prompt(
            snapshot,
            previous_digest=previous_digest,
            web_search_by_ticker=web_search_by_ticker,
            prompt_template_path=self.settings.digest_prompt_path,
        )
```

- [ ] **Step 4: Run digest tests**

Run:

```bash
uv run pytest tests/test_digest.py -v
```

Expected: PASS.

- [ ] **Step 5: Run provider tests**

Run:

```bash
uv run pytest tests/test_web_search.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 4**

Run:

```bash
git add src/finage/digest.py tests/test_digest.py
git commit -m "feat: enrich digests with web search"
```

---

### Task 5: Update The Digest Prompt Text

**Files:**
- Modify: `src/finage/prompts/wsb_digest.md`
- Modify: `tests/test_digest.py`

- [ ] **Step 1: Add failing prompt contract test**

Add this test to `tests/test_digest.py`:

```python
def test_default_digest_prompt_describes_web_search_evidence() -> None:
    prompt = build_digest_prompt(make_snapshot())

    assert "Web search evidence may show recent news" in prompt
    assert "Use web search to ground catalysts" in prompt
    assert "This is market intelligence, not financial advice." in prompt
    assert "A brief \"watchlist read\"" in prompt
```

- [ ] **Step 2: Run the prompt test to verify it fails**

Run:

```bash
uv run pytest tests/test_digest.py::test_default_digest_prompt_describes_web_search_evidence -v
```

Expected: FAIL because the current prompt does not describe web search evidence.

- [ ] **Step 3: Replace the default digest prompt**

Replace `src/finage/prompts/wsb_digest.md` with:

```markdown
Create a Telegram-friendly market momentum digest from this JSON evidence.

Goal:
Identify short-term retail momentum signals based on Reddit activity, then use web search results as supporting context for recent real-world catalysts.

Evidence types:
- Reddit evidence shows retail attention, sentiment, narratives, and repeated discussion patterns.
- Web search evidence may show recent news, earnings, analyst notes, product updates, regulatory events, or other catalysts.
- Do not treat web search results as proof unless the title/highlights clearly support the claim.
- If Reddit and web evidence disagree, call that out briefly.

For each top ticker:
- Ticker + momentum score (High / Medium / Low)
- Why it is trending on Reddit
- Sentiment (Bullish / Bearish / Mixed) with confidence level
- Key Reddit narratives, emphasizing repeated themes over one-off opinions
- Recent web context, if available, including relevant URLs
- Catalysts: separate confirmed/news-based catalysts from Reddit rumors
- Evidence: quote or paraphrase representative Reddit posts/comments and cite web result URLs when used

Rules:
- Prioritize signal over hype; ignore low-effort memes unless dominant
- Weigh repeated Reddit ideas more than isolated comments
- Use web search to ground catalysts, not to replace Reddit momentum analysis
- Do NOT fabricate missing data
- Do NOT claim a web search result says something unless it is present in the provided title, highlights, or text
- Compare against the previous digest when available and highlight any key patterns that emerge. Do this only if it is relevant.
- This is market intelligence, not financial advice.

Output:
- One-line title
- Concise bullets
- Max 5-7 tickers
- Use simple Markdown formatting, especially `**bold**` for ticker names and section headers
- Include URLs only when they support a concrete catalyst or clarification

End with:
A brief "watchlist read" that separates short-term momentum setups from longer-term items to monitor, based only on the provided evidence.

Previous Digest JSON:
{previous_digest_json}

Evidence JSON:
{evidence_json}
```

- [ ] **Step 4: Run digest tests**

Run:

```bash
uv run pytest tests/test_digest.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 5**

Run:

```bash
git add src/finage/prompts/wsb_digest.md tests/test_digest.py
git commit -m "prompt: account for web search context"
```

---

### Task 6: Final Verification And Documentation Check

**Files:**
- Review: `README.md`
- Review: `docs/superpowers/specs/2026-04-28-exa-web-search-design.md`
- Review: all changed source and test files

- [ ] **Step 1: Run the full test suite**

Run:

```bash
uv run pytest
```

Expected: all tests PASS.

- [ ] **Step 2: Run a local digest prompt smoke check without network calls**

Run:

```bash
uv run python - <<'PY'
from finage.models import PostEvidence, TickerEvidence, TrendingTicker, WsbSnapshot
from finage.prompting import render_digest_prompt

snapshot = WsbSnapshot(
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

prompt = render_digest_prompt(snapshot)
print("Evidence JSON" in prompt)
print("Previous Digest JSON" in prompt)
print("Web search evidence may show recent news" in prompt)
PY
```

Expected output:

```text
True
True
True
```

- [ ] **Step 3: Check git status**

Run:

```bash
git status --short
```

Expected: no uncommitted changes if prior tasks were committed individually.

