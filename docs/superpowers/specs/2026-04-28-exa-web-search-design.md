# Exa Web Search Design

## Context

Finage currently generates a Telegram-friendly WSB momentum digest by collecting trending tickers, scraping Reddit evidence, rendering a prompt, and sending that prompt to Gemini. The next step is to add a general web search capability using Exa so the digest, and future slash commands, can access recent web context without each feature implementing search on its own.

The first version should be an internal capability only. It should enrich LLM prompts, not expose a `/search` command yet.

## Goals

- Add a reusable web search primitive backed by Exa.
- Keep the primitive generic enough for future slash commands and agentic workflows.
- Use ticker-focused web search as the first consumer.
- Plumb web search results into the digest prompt as supporting context.
- Keep digest generation working if Exa is missing or temporarily fails.
- Avoid building a custom agent/tool harness before a concrete workflow needs one.

## Non-Goals

- No public Telegram `/search` command in this phase.
- No custom multi-agent tool runner in this phase.
- No persistent historical search database.
- No full-page crawling by default.

## Architecture

Add a new `finage.web_search` module with a small provider boundary:

- `WebSearchProvider` protocol with an async `search(query, options) -> WebSearchResponse` method.
- `ExaWebSearchProvider` using Exa's async Python SDK.
- Pydantic models for `WebSearchOptions`, `WebSearchResult`, and `WebSearchResponse`.
- `create_web_search_provider(settings)` factory, mirroring the existing LLM provider pattern.

The provider should normalize Exa SDK objects into project-owned models before returning them. Callers should not depend on Exa-specific response objects.

Default search behavior should be conservative:

- Small result count.
- Highlights/snippets by default.
- Optional full text only when the caller asks for it.
- No direct user-facing command yet.

Future agentic workflows can wrap `WebSearchProvider` as a tool for a library such as LangChain, LlamaIndex, or pydantic-ai. Finage should not create that harness now.

## Digest Data Flow

The digest flow should become:

1. `WsbCollector.collect()` produces the current `WsbSnapshot`.
2. `DigestService.generate()` identifies ticker symbols that already have Reddit evidence.
3. For each relevant ticker, `DigestService` builds a finance-oriented query, such as `"{ticker} stock latest news earnings analyst catalyst"`.
4. `DigestService` calls the generic web search provider with ticker-specific options.
5. Results are normalized into a compact enrichment payload.
6. `render_digest_prompt()` receives the snapshot, previous digest, and optional web search enrichment.
7. `build_digest_payload()` attaches search results under the matching ticker in the prompt JSON.
8. Gemini receives one evidence payload that distinguishes Reddit evidence from web search evidence.

The `WsbSnapshot` artifact should remain Reddit/social evidence only. Web search enrichment should be assembled for prompt context rather than written into the snapshot model. This keeps collection and LLM context assembly separate.

## Prompt Payload Shape

Each ticker entry in `ticker_evidence` may include a `web_search` block:

```json
{
  "ticker": "TSLA",
  "posts": [],
  "web_search": {
    "query": "TSLA stock latest news earnings analyst catalyst",
    "results": [
      {
        "title": "Example result",
        "url": "https://example.com/tesla-news",
        "published_date": "2026-04-28",
        "author": "Example Author",
        "highlights": ["Relevant highlighted excerpt"],
        "text": null,
        "score": 0.82
      }
    ]
  }
}
```

The result model should support:

- `title`
- `url`
- `published_date`
- `author`
- `highlights`
- optional `text`
- optional `score`

## Prompt Update

Update `src/finage/prompts/wsb_digest.md` to treat Reddit evidence and web search evidence as different source types:

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

## Configuration

Add optional Exa and search settings:

- `EXA_API_KEY`: optional. If absent, web enrichment is disabled.
- `WEB_SEARCH_PROVIDER`: default `exa` when `EXA_API_KEY` exists; otherwise no provider.
- `WEB_SEARCH_NUM_RESULTS`: default `3`.
- `WEB_SEARCH_CONTENT_MODE`: default `highlights`; supported values should include `highlights`, `text`, and `none`.
- `WEB_SEARCH_TIMEOUT_SECONDS`: default around `15`.
- `DIGEST_WEB_SEARCH_ENABLED`: default true when Exa is configured, false otherwise.

Existing digest behavior should remain valid when no Exa key is configured.

## Failure Behavior

Web search failures must not fail digest generation.

If a ticker search fails:

- Log the exception with the ticker and query.
- Omit the failed search results from the prompt payload.
- Continue with the remaining tickers.

The prompt should not receive a generic "web search unavailable" note by default, because that adds noise and may distract the model from the evidence that exists.

## Testing

Add focused tests for:

- Exa result normalization into project-owned models.
- Missing `EXA_API_KEY` disables web enrichment cleanly.
- Search failures do not fail digest generation.
- `build_digest_payload()` includes `web_search` results under the correct ticker.
- Existing digest behavior remains unchanged when no enrichment is present.
- The updated prompt still includes `{evidence_json}` and `{previous_digest_json}`.

## Future Considerations

- Ticker queries can include company names later if a new data source provides issuer metadata. The first version should use ticker symbols only because ApeWisdom currently provides tickers, not issuer metadata.
- Finance source domain filters can be revisited after reviewing output quality. The first version should avoid source restrictions and let Exa rank results.
