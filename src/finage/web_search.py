from __future__ import annotations

import logging
from dataclasses import dataclass
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
