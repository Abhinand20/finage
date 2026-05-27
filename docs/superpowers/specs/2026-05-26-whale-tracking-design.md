# Whale Tracking Feature — Design Spec

**Date:** 2026-05-26
**Status:** Approved
**Scope:** Track 13F holdings for a curated top-10 institutional fund watchlist, compute quarter-over-quarter momentum signals, expose `/whale <TICKER>` and `/whales`, and surface whale-momentum convergence with existing social and congress signals.

---

## 1. Goals

1. Ingest the latest Form 13F-HR filing for each fund in a fixed top-10 watchlist via the SEC EDGAR data source.
2. Persist a local snapshot per fund and per ticker so the bot can answer ticker and digest queries without re-fetching.
3. Compute deterministic per-ticker whale momentum signals: NEW positions, BIG ADD increases, REDUCTION decreases, FULL EXIT closures, and MULTI-FUND CLUSTER events.
4. Combine whale activity with existing social momentum and congress signals to score convergence.
5. Expose two Telegram commands: `/whale <TICKER>` for ticker-focused analysis, `/whales` for a top whale-momentum digest.
6. Provide a daily/weekly `finage whale refresh` CLI command suitable for cron or PM2.

---

## 2. Data Source

We use [`edgartools`](https://github.com/dgunning/edgartools) over the official SEC EDGAR data.

- `set_identity("finage@<your-domain>")` is required by the SEC; finage reads this from `EDGAR_IDENTITY` env var.
- For each fund CIK we call `Company(cik).get_filings(form="13F-HR")` and use the latest filing object.
- The 13F filing object exposes:
  - `management_company_name`
  - `report_period`
  - `total_holdings`, `total_value`
  - `holdings_data()` → pandas DataFrame
  - `compare_holdings()` → comparison object with `.data` DataFrame and a `Status` column (`NEW`, `CLOSED`, `INCREASED`, `DECREASED`, `UNCHANGED`).

13F filings are quarterly with up to a 45-day filing delay. The feature treats this as “quarterly conviction signal”, not real time.

---

## 3. Top-10 Fund Watchlist

Fixed in code as `WHALE_WATCHLIST` in `src/finage/whale.py`:

| Fund | Manager | CIK |
|---|---|---:|
| Berkshire Hathaway | Warren Buffett | `1067983` |
| Pershing Square | Bill Ackman | `1336528` |
| Scion Asset Management | Michael Burry | `1649339` |
| Appaloosa | David Tepper | `1656456` |
| Duquesne Family Office | Stanley Druckenmiller | `1536411` |
| Bridgewater Associates | Ray Dalio | `1350694` |
| Citadel Advisors | Ken Griffin | `1423053` |
| Tiger Global | Chase Coleman | `1167483` |
| Coatue Management | Philippe Laffont | `1135730` |
| Soros Fund Management | George Soros | `1029160` |

Each entry has a slug, display name, manager, and CIK. The slug is used for the on-disk filename so the watchlist can grow without storage churn.

Watchlist CIKs are verified by the bootstrap refresh — any entry that fails to resolve is logged once and skipped without aborting the rest of the refresh.

---

## 4. Settings (`src/finage/settings.py` additions)

```python
edgar_identity: str | None = None
whale_lookback_quarters: int = 2
whale_cache_ttl_hours: int = 24
whale_min_signal_score: float = 2.0
whale_enabled: bool = True
```

`whale_enabled` auto-flips to `False` when `EDGAR_IDENTITY` is absent so the rest of the bot keeps working.

`.env.example` adds:

```dotenv
EDGAR_IDENTITY=
WHALE_LOOKBACK_QUARTERS=2
WHALE_CACHE_TTL_HOURS=24
WHALE_MIN_SIGNAL_SCORE=2.0
```

---

## 5. Data Models (`src/finage/models.py` additions)

```python
class WhaleHolding(BaseModel):
    ticker: str
    cusip: str | None
    shares: int
    value_usd: int
    weight: float                   # share of total fund value, 0.0–1.0


class WhaleChange(BaseModel):
    status: Literal["NEW", "INCREASED", "DECREASED", "CLOSED", "UNCHANGED"]
    ticker: str
    shares_delta: int
    shares_delta_pct: float | None
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
    filing_accession: str | None
    total_holdings: int
    total_value_usd: int
    holdings: list[WhaleHolding]
    changes: list[WhaleChange]
    fetched_at: datetime


class WhaleSignal(BaseModel):
    ticker: str
    total_score: float
    convergence_score: float | None
    fund_count: int
    new_count: int
    increased_count: int
    decreased_count: int
    closed_count: int
    total_value_usd: int
    largest_position_fund: str
    largest_position_value_usd: int
    funds: list[str]
    has_social_overlap: bool
    has_congress_overlap: bool
    labels: list[str]               # NEW POSITION, BIG ADD, MULTI-FUND CLUSTER, etc.


class WhaleSnapshot(BaseModel):
    fetched_at: datetime
    funds: list[WhaleFundSnapshot]
    signals: list[WhaleSignal]
```

---

## 6. Storage Layout

```
data/whale/
  latest.json                      ← WhaleSnapshot
  signals.json                     ← list[WhaleSignal] for fast lookup
  by_fund/
    berkshire-hathaway.json        ← WhaleFundSnapshot
    pershing-square.json
  by_ticker/
    NVDA.json                      ← per-ticker rollup: list[WhaleFundActivity]
    TSLA.json
```

`by_ticker/{TICKER}.json` is precomputed during refresh so `/whale <TICKER>` is a single file read. Each ticker file contains one entry per fund that holds or recently held the ticker, with shares, value, status, and report period.

---

## 7. Module: `src/finage/whale.py`

### 7.1 `WhaleCollector`

```python
class WhaleCollector:
    def __init__(
        self,
        settings: Settings,
        *,
        edgar_module=None,           # injected for tests
        now_provider=None,
    ): ...

    async def refresh(self) -> WhaleSnapshot:
        """Fetch latest 13F for every fund in the watchlist, persist, and return snapshot."""

    async def get_or_fetch(self) -> WhaleSnapshot:
        """Return cached snapshot if age < whale_cache_ttl_hours, else refresh."""

    def get_cached(self) -> WhaleSnapshot | None: ...

    def trades_for_ticker(self, snapshot: WhaleSnapshot, ticker: str) -> list[WhaleFundSnapshot]:
        """Return fund snapshots that include the ticker, filtered to relevant holdings/changes."""
```

The collector calls edgartools synchronously inside `asyncio.to_thread` so it does not block the event loop on the Pi. SEC requests are throttled to one filing at a time. Per-fund failures are logged and the snapshot is built with whatever funds succeeded; this prevents a single broken filing from killing the refresh.

### 7.2 `WhaleAnalyzer`

```python
class WhaleAnalyzer:
    def top_signals(
        self,
        funds: list[WhaleFundSnapshot],
        *,
        min_score: float,
    ) -> list[WhaleSignal]: ...

    def apply_convergence(
        self,
        signals: list[WhaleSignal],
        *,
        reddit_tickers: list[TrendingTicker],
        congress_signals: list[CongressSignal] | None,
    ) -> list[WhaleSignal]: ...

    def format_ticker_brief(self, signal: WhaleSignal, fund_lines: list[str]) -> str: ...
```

Pure logic, no I/O, no network.

### 7.3 Signal Scoring

```
score(change) = action_weight × magnitude_weight × fund_quality_weight
ticker_score  = sum(score(c) for c in ticker_changes) × cluster_bonus

convergence_score = ticker_score
                  + social_overlap_score
                  + congress_overlap_score
```

| Action | Weight |
|---|---:|
| NEW | 3.0 |
| INCREASED | 2.0 |
| DECREASED | -1.0 |
| CLOSED | -2.0 |
| UNCHANGED | 0.0 |

| Magnitude (% shares change) | Weight |
|---|---:|
| ≥ +100% or NEW | 2.0 |
| ≥ +25% | 1.5 |
| ≥ +10% | 1.0 |
| < 10% | 0.5 |

| Fund quality (rank within watchlist) | Weight |
|---|---:|
| Top 3 (Buffett/Ackman/Burry) | 1.3 |
| Mid 4 | 1.1 |
| Lower 3 | 1.0 |

| Fund cluster (same ticker, same direction) | Bonus |
|---|---:|
| 1 fund | ×1.0 |
| 2 funds | ×1.5 |
| 3+ funds | ×2.0 |

| Social rank (ApeWisdom top 25) | Bonus |
|---|---:|
| 1–3 | +3.0 |
| 4–10 | +2.0 |
| 11–25 | +1.0 |
| not ranked | +0.0 |

| Congress overlap | Bonus |
|---|---:|
| Any congress signal above min score | +2.0 |

---

## 8. Convergence With Social + Congress

When local artifacts for ApeWisdom (`data/latest_wsb_snapshot.json`) or congress (`data/congress/history.json`) are available, the whale analyzer reads them at scoring time. If unavailable, convergence scoring degrades gracefully to whale-only ranking.

Labels are precomputed in code so Gemini does not invent or reorder them:

- `NEW POSITION`
- `BIG ADD` (≥ +25% increase by any fund)
- `MULTI-FUND CLUSTER` (≥ 2 funds same direction)
- `REDUCTION` (any DECREASED change)
- `FULL EXIT` (any CLOSED change)
- `WHALE + SOCIAL` (overlap with ApeWisdom top 25)
- `WHALE + CONGRESS` (overlap with congress signals)

---

## 9. Telegram Command: `/whale <TICKER>`

**Handler:** `TelegramDigestBot.whale()`
**Analysis method:** `MomentumAnalysisService.whale(ticker)`

Flow:
1. `WhaleCollector.get_or_fetch()` — cache hit if < 24h.
2. `WhaleCollector.trades_for_ticker(snapshot, ticker)`.
3. If no fund holds the ticker → Gemini still receives a prompt with “No top-10 whale activity in local history” plus social/congress context.
4. Otherwise the analyzer produces per-fund lines, signal labels, and a one-paragraph summary.
5. Gemini converts the deterministic structure into a Telegram-ready brief.

Prompt template `src/finage/prompts/whale_ticker.md`:

```markdown
You are analyzing institutional Form 13F holdings for {ticker} ({asset_description}).

Watchlist funds covered:
{watchlist_summary}

Whale activity for {ticker} (precomputed by code, sorted by latest filing):
{fund_lines}

Signal labels: {labels}

Current social momentum context:
- ApeWisdom Reddit rank: {reddit_rank}
- Recent congressional activity: {congress_summary}

Provide a concise analysis covering:
1. Overall whale pattern: who added, who reduced, who exited.
2. Conviction signal: NEW, BIG ADD, MULTI-FUND CLUSTER, or REDUCTION dominant?
3. Convergence with social/congress data.
4. Caveats: 13F is quarterly and delayed up to 45 days.

Do not invent funds. Do not present this as financial advice. Keep the response under 400 words.
```

---

## 10. Telegram Command: `/whales`

A digest-style top-N rollup across the watchlist. Default N = 5.

Flow:
1. `WhaleCollector.get_or_fetch()`.
2. `WhaleAnalyzer.top_signals(...)` then `apply_convergence(...)`.
3. Filter to signals above `whale_min_signal_score`.
4. Pass to a Gemini prompt that writes a Telegram-friendly “Whale Momentum” brief.

Prompt template `src/finage/prompts/whales_digest.md` mirrors the congress digest prompt: deterministic ordering and labels precomputed by code; Gemini narrates only.

Output shape:

```markdown
## Whale Momentum
*Latest 13F filings, top 10 watchlist*

- **NVDA** — BIG ADD · Citadel +22%, Coatue +18% · $1.4B exposure · WHALE + SOCIAL
- **GOOGL** — NEW POSITION · Pershing Square opened $320M · MULTI-FUND CLUSTER
- **TSLA** — REDUCTION · 3 funds trimmed exposure · WHALE + SOCIAL
```

---

## 11. Daily Digest Integration

The existing `DigestService.generate()` is extended:

1. After the congress section is rendered (if enabled), call `WhaleCollector.get_or_fetch()` if `whale_enabled`.
2. Build top signals, apply convergence using the same WSB snapshot and (optionally) congress signals.
3. Render `prompts/whale_digest.md` and append the resulting block after the congress section.

If `whale_enabled` is false or the snapshot is missing, the section is skipped silently.

---

## 12. CLI

`finage whale refresh` calls `WhaleCollector.refresh()` and prints:

```text
Whale refresh complete: funds=10 with_filings=9 skipped=1 tickers=243 signals=18 cache=data/whale
```

Intended to be run once per day via cron/PM2. The refresh fetches sequentially with edgartools’ built-in rate limiting; total runtime should be well under one minute on a Pi.

---

## 13. Error Handling

- Missing `EDGAR_IDENTITY` → `whale_enabled` is False at startup, all whale code paths return early.
- One fund filing fails → log warning, continue with remaining funds, mark fund as `with_filings=False` in metadata.
- All filings fail → keep prior cache if present and log error; surface a clear message in `/whale` and `/whales`.
- Local social/congress artifacts missing → convergence scoring degrades to whale-only.

---

## 14. Analysis Quality Rules

1. **Code ranks, Gemini narrates.** Deterministic labels and ordering happen in `WhaleAnalyzer`.
2. **13F is quarterly and delayed.** Prompts and output always mention this caveat for `/whale` and `/whales`.
3. **No fabricated funds or tickers.** Gemini only references the structured input.
4. **Cluster signals beat single-fund signals.** Multi-fund moves rank above isolated moves unless the single move is unusually large.
5. **Convergence is strongest signal.** Whale + social + congress overlap ranks above any single-source signal.

---

## 15. Test Plan

| Test Area | Coverage |
|---|---|
| Watchlist | Top-10 watchlist constants are stable, deduplicated, and reference valid CIK strings. |
| Settings | Defaults, env wiring, `whale_enabled` auto-disable without `EDGAR_IDENTITY`. |
| Models | Whale models validate with realistic field values. |
| Collector | Mocked edgartools yields a `WhaleSnapshot`; per-fund failures do not abort the refresh. |
| Storage | `latest.json`, `signals.json`, `by_fund/`, `by_ticker/` are all written; ticker file aggregates per-fund activity. |
| Cache | `get_or_fetch()` serves fresh cache without network; stale cache returned if refresh fails. |
| Analyzer | Scoring weights produce expected NEW > INCREASED > DECREASED ordering with cluster bonuses. |
| Convergence | Social and congress overlaps add expected bonuses; missing artifacts degrade gracefully. |
| Digest integration | Whale section appended only when enabled; deterministic labels preserved in the Gemini prompt. |
| `/whale` command | Valid ticker, invalid ticker, no-data Gemini path, stale-cache path. |
| `/whales` command | Returns a top-N brief; cache-only mode works. |
| CLI | `finage whale refresh` parses, runs, and prints the summary line. |

---

## 16. New & Modified Files

### New

| File | Purpose |
|---|---|
| `src/finage/whale.py` | `WhaleCollector`, `WhaleAnalyzer`, `WHALE_WATCHLIST`. |
| `src/finage/prompts/whale_ticker.md` | Prompt for `/whale TICKER`. |
| `src/finage/prompts/whales_digest.md` | Prompt for `/whales`. |
| `src/finage/prompts/whale_digest.md` | Prompt fragment appended to the daily digest. |
| `tests/test_whale.py` | Watchlist, collector, analyzer, storage, CLI parser tests. |

### Modified

| File | Change |
|---|---|
| `src/finage/models.py` | Add `WhaleHolding`, `WhaleChange`, `WhaleFundSnapshot`, `WhaleSignal`, `WhaleSnapshot`. |
| `src/finage/settings.py` | Add `edgar_identity`, `whale_*` settings, auto-disable rule. |
| `src/finage/prompting.py` | Add prompt rendering helpers for whale templates. |
| `src/finage/digest.py` | Append whale section after congress section when enabled. |
| `src/finage/analysis.py` | `whale()` and `whales()` methods on `MomentumAnalysisService`. |
| `src/finage/telegram_bot.py` | Register `/whale` and `/whales` handlers. |
| `src/finage/cli.py` | Add `finage whale refresh` command. |
| `.env.example` | Document `EDGAR_IDENTITY` and `WHALE_*` settings. |
| `pyproject.toml` | Add `edgartools` dependency. |

---

## 17. Out of Scope

- Custom fund watchlist via env (v2 only; v1 keeps the curated top 10).
- Real-time alerting when a new 13F drops outside the daily refresh.
- Position prices/valuation reconstruction beyond what edgartools provides.
- Mutual fund / N-PORT support — 13F-only for v1.
- Backtesting whale signals against returns.
