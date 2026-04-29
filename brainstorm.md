## Time Log

> 04/25/2026: 4.30PM - 5.00PM

#### High-level goals

- Daily digestible summary of "momentum" stocks through alt data sources like Reddit.
- No fluff, simple telegram bot, hosted on rpi
- Secondary pipeline of senate trades being scraped and analyzed.

> 04/25/2026: 5.00PM - 6.00PM

#### Tasks

Phase 1 (Proof of concept):
M1: End to End setup for scraping WSB. [DONE]
M2: Extend to other subreddits. [DONE]
M3: Add more /slash commands for different features. [DONE]
M4: Support senate trades.

Phase 2 (This is the fun part!):
M5: Move to a multi-agent setup, provide tools for exploration, analysis etc.
M6: Extend to a richer UI and better data visualization.

#### TODOs:
- M1.1: Add search results for latest information on a ticker. [DONE]
- M3.1: Add social momentum alerts for watchlist tickers when rank, mentions, or evidence score crosses a threshold.
- M3.2: Add a daily/weekly archive so `/movers` can compare against more than the latest saved digest.
- M4.1: Add senate trade ingestion and a `/senate <stock>` command that cross-references disclosures with social momentum.
- M6.1: Add a lightweight dashboard for digest history, ticker cards, source links, and trend deltas.

#### Progress Notes:

- 04/25/2026
  - M1 now has a Python/uv MVP that scrapes ticker-first WSB evidence, generates a Gemini-powered digest, and exposes it through both `finage digest preview/send` and a restricted Telegram bot.
  - The app persists latest-run JSON artifacts, feeds the previous digest into the next run for continuity, keeps prompts configurable in Markdown, and includes tests plus Raspberry Pi deployment notes.
- 04/28/2026 - 04/29/2026
  - M2 expanded the collector from a WSB-only flow to a broader stock subreddit scan using the configured ApeWisdom filters and Reddit evidence sources.
  - M1.1 added optional Exa web search enrichment for digest prompts, including configurable result count, content mode, timeout, and explicit enable/disable behavior.
  - M3 added `/live`, `/ticker <stock>`, `/why <stock>`, `/movers`, and `/health` so the Telegram bot can answer focused momentum questions without always writing a new digest artifact.
  - Prompt iteration now supports bundled prompt selection with `DIGEST_PROMPT_BUNDLE`, plus arbitrary local prompt files through `DIGEST_PROMPT_PATH`.