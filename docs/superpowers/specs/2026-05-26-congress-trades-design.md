# Congress Trades Feature — Design Spec

**Date:** 2026-05-26  
**Status:** Approved  
**Scope:** Add congressional trading data (Senate + House) to finage as a daily async refresh, an appended section in the Gemini digest, and an on-demand `/senate <TICKER>` Telegram command.

---

## 1. Goals

1. Ingest Senate and House financial disclosures daily via FMP's bulk latest endpoints.
2. Persist a growing historical ledger locally so the bot has context beyond the current fetch window.
3. Surface high-conviction trades in the daily Gemini digest as an appended section.
4. Flag "convergence" when a congressional ticker also appears in the Reddit social momentum top-10.
5. Expose an on-demand `/senate <TICKER>` command that returns a Gemini analysis of all known trades for that ticker.

---

## 2. Data Source

| Endpoint | Parameters |
|----------|-----------|
| `https://financialmodelingprep.com/stable/senate-latest` | `page=0&limit=250&apikey=KEY` |
| `https://financialmodelingprep.com/stable/house-latest` | `page=0&limit=250&apikey=KEY` |

- **2 API calls per daily refresh** — safe for FMP free tier (250 calls/day quota).
- Max 250 records per request; page 0 covers ~2–4 weeks of recent disclosures per chamber.
- **Bootstrap run** (first execution): paginate through pages 0–N until `transactionDate` falls outside `congress_lookback_days`. Subsequent daily runs fetch page 0 only.
- Required env var: `FMP_API_KEY` (already in `.env.example` for the Stocknear reference; added to finage's settings).

**Raw fields returned:**

| FMP Field | Normalized To |
|-----------|--------------|
| `symbol` | `ticker` (uppercased) |
| `firstName` + `lastName` | `representative` |
| `transactionDate` | `transaction_date` (date) |
| `disclosureDate` | `disclosure_date` (date) |
| `type` | `transaction_type`: `"Purchase"` → `"Bought"`, `"Sale"` → `"Sold"`, else `"Exchange"` |
| `amount` | `amount` (bucketed label — see §3.1) |
| `assetDescription` | `asset_description` |
| *(source endpoint)* | `chamber`: `"Senate"` or `"House"` |

---

## 3. Data Models (`src/finage/models.py` additions)

### 3.1 Amount Bucketing

```
"$1,001 - $15,000"        → "$1K-$15K"
"$15,001 - $50,000"       → "$15K-$50K"
"$50,001 - $100,000"      → "$50K-$100K"
"$100,001 - $250,000"     → "$100K-$250K"
"$250,001 - $500,000"     → "$250K-$500K"
"$500,001 - $1,000,000"   → "$500K-$1M"
"$1,000,001 - $5,000,000" → "$1M-$5M"
(anything above)           → "Over $5M"
```

Unmapped strings are stored as-is.

### 3.2 `CongressTrade`

```python
class CongressTrade(BaseModel):
    trade_id: str               # sha256[:12] of ticker|representative|transaction_date|transaction_type|amount
    ticker: str
    asset_description: str
    representative: str
    chamber: Literal["Senate", "House"]
    transaction_type: Literal["Bought", "Sold", "Exchange"]
    amount: str                 # bucketed label
    transaction_date: date
    disclosure_date: date
```

### 3.3 `CongressSnapshot`

```python
class CongressSnapshot(BaseModel):
    fetched_at: datetime
    total_trades: int
    new_trades: int             # count of trades added in this refresh cycle
    trades: list[CongressTrade] # full history, sorted by disclosure_date desc
```

### 3.4 `CongressSignal`

Per-ticker aggregation used by the digest and `/senate` command.

```python
class CongressSignal(BaseModel):
    ticker: str
    asset_description: str
    total_score: float
    buy_count: int
    sell_count: int
    trade_count: int
    largest_amount: str         # highest-value bucket seen
    politicians: list[str]      # deduplicated representative names
    latest_disclosure: date
    is_cluster: bool            # 2+ buys on this ticker within the lookback window
    is_bicameral: bool          # trades from both Senate AND House members on same ticker
```

Note: Party affiliation is not available from FMP's latest endpoints and is out of scope (see §12). `is_bicameral` is used instead as a signal that interest spans both chambers.

---

## 4. Settings (`src/finage/settings.py` additions)

```python
fmp_api_key: str | None = None
congress_lookback_days: int = 30          # window for digest + signal scoring
congress_cache_ttl_hours: int = 12        # TTL for the pull-with-cache strategy
congress_min_signal_score: float = 2.0    # minimum score to appear in digest
congress_enabled: bool = True             # toggle entire feature off if no FMP key
```

`congress_enabled` is automatically set to `False` if `fmp_api_key` is absent, so the bot degrades gracefully — digest and `/senate` skip the congress section with a single log line rather than erroring.

---

## 5. Storage Layout

```
data/congress/
  history.json        ← CongressSnapshot (full ledger, append-only)
  latest.json         ← CongressSnapshot (last fetch metadata + full trades list — same shape)
  by_ticker/
    NVDA.json         ← list[CongressTrade] filtered to this ticker, sorted by disclosure_date desc
    TSLA.json
    ...
```

### 5.1 Deduplication & Append Logic

```
trade_id = sha256(f"{ticker}|{representative}|{transaction_date}|{transaction_type}|{amount}".encode()).hexdigest()[:12]
```

On each refresh:
1. Fetch both FMP endpoints (Senate + House).
2. Normalize all fields; compute `trade_id` for each record.
3. Load `history.json`; build `existing_ids = {t.trade_id for t in history.trades}`.
4. Filter to `new_trades = [t for t in fetched if t.trade_id not in existing_ids]`.
5. Append `new_trades` to `history.trades`; sort by `disclosure_date` descending.
6. Write updated `history.json` and `latest.json` (with `new_trades` count).
7. For each ticker that had new trades, rewrite `by_ticker/{TICKER}.json`.

`latest.json` and `history.json` share the same `CongressSnapshot` schema. `latest.json` has `new_trades > 0` only immediately after a refresh that found new data.

---

## 6. Module: `src/finage/congress.py`

### 6.1 `CongressCollector`

Responsible for fetching, normalizing, deduplicating, and persisting trade data.

**Public interface:**

```python
class CongressCollector:
    def __init__(self, settings: Settings): ...

    async def get_or_fetch(self) -> CongressSnapshot:
        """Return cached snapshot if age < congress_cache_ttl_hours, else re-fetch."""

    async def fetch(self) -> CongressSnapshot:
        """Unconditional fetch from FMP, deduplicate, persist, return updated snapshot."""

    def get_cached(self) -> CongressSnapshot | None:
        """Return snapshot from disk if it exists, None otherwise. No network call."""

    def trades_for_ticker(self, snapshot: CongressSnapshot, ticker: str) -> list[CongressTrade]:
        """Read from by_ticker/{ticker}.json (all history). Falls back to filtering snapshot.trades."""
```

**Fetch internals:**
- `httpx.AsyncClient` with 20s timeout (matches existing `web_search.py` pattern).
- Fetch Senate and House concurrently via `asyncio.gather`.
- On HTTP error: log warning, return stale cache if available; raise only if cache is also absent.
- Bootstrap mode: if `history.json` is absent or empty, paginate pages 0, 1, 2, … until fetched records have `transaction_date < today - congress_lookback_days * 3`. This ensures a rich initial history without hammering the API.

### 6.2 `CongressAnalyzer`

Pure analysis logic; no I/O, no network. Takes `list[CongressTrade]` as input.

```python
class CongressAnalyzer:
    def top_signals(
        self,
        trades: list[CongressTrade],
        lookback_days: int,
        min_score: float,
    ) -> list[CongressSignal]:
        """
        Filter to trades within lookback_days by disclosure_date.
        Compute signal score per trade, aggregate per ticker, return ranked list.
        """

    def overlap_tickers(
        self,
        signals: list[CongressSignal],
        reddit_tickers: list[str],
    ) -> set[str]:
        """Return tickers that appear in both signals and reddit_tickers."""

    def format_signal_line(self, signal: CongressSignal, overlap: bool) -> str:
        """
        Return a single Markdown bullet for one ticker signal.
        Example:
          - **NVDA** — 3 buys · $50K–$500K · Sen. Tuberville, Rep. Pelosi · Cluster · ⚡ CONVERGENCE
        """
```

### 6.3 Signal Scoring

```
score(trade) = amount_weight(trade.amount)
             × type_weight(trade.transaction_type)
             × recency_weight(trade.disclosure_date)

ticker_score = sum(score(t) for t in ticker_trades)
             × cluster_bonus(buy_count)
```

**Weights:**

| Amount | Weight |
|--------|--------|
| Over $5M | 6.0 |
| $1M-$5M | 5.0 |
| $500K-$1M | 3.5 |
| $250K-$500K | 2.5 |
| $100K-$250K | 2.0 |
| $50K-$100K | 1.5 |
| $15K-$50K | 1.0 |
| $1K-$15K | 0.5 |

| Type | Weight |
|------|--------|
| Bought | 1.5 |
| Exchange | 1.0 |
| Sold | 0.7 |

| Disclosure recency | Weight |
|--------------------|--------|
| ≤ 7 days | 1.5 |
| 8–30 days | 1.0 |
| 31–60 days | 0.6 |
| > 60 days | 0.3 |

| Buy cluster (same ticker, lookback window) | Bonus |
|--------------------------------------------|-------|
| 1 buyer | ×1.0 |
| 2 buyers | ×1.5 |
| 3+ buyers | ×2.0 |

---

## 7. Digest Integration (`src/finage/digest.py`)

The `DigestService.generate()` method is extended as follows:

1. Call `CongressCollector(settings).get_or_fetch()` concurrently with `WsbCollector.collect()` using `asyncio.gather`. If `congress_enabled` is `False`, skip.
2. Compute `signals = CongressAnalyzer().top_signals(snapshot.trades, lookback_days, min_score)`.
3. Compute `overlap = CongressAnalyzer().overlap_tickers(signals, wsb_tickers)` where `wsb_tickers` are the top-10 tickers from the `WsbSnapshot`.
4. Build a `congress_section` string using `format_signal_line` for each signal, marking overlaps with `⚡ CONVERGENCE`.
5. Inject `congress_section` into the Gemini prompt as a clearly delimited block after the WSB evidence (see §7.1).

The existing digest prompt file (`prompts/wsb_digest.md`) is **not modified**. The congress section is appended to the assembled prompt string in Python, keeping concerns separate.

### 7.1 Prompt Injection

```
--- CONGRESSIONAL TRADING DATA ---
Disclosures filed in the last {lookback_days} days. Signals above score threshold only.

{congress_section_markdown}

Overlap with social momentum top-10: {overlap_list}

Instructions: After your main momentum analysis, add a "## Congressional Activity" section.
Format it as bullet points, one per ticker. Include politician names, amount ranges, and
buy/sell direction. Mark any ticker in the overlap list as "⚡ CONVERGENCE".
If no signals exist above the threshold, write a single line:
"No notable congressional activity this period."
--- END CONGRESSIONAL TRADING DATA ---
```

### 7.2 Example Digest Output

```markdown
## Congressional Activity
*Disclosures filed in the last 30 days*

- **NVDA** — 3 purchases · $50K–$500K · Sen. Tuberville, Rep. Pelosi, Rep. Crenshaw · Cluster · Bicameral ⚡ CONVERGENCE (Reddit rank #2)
- **RTX** — 1 purchase · $100K–$250K · Rep. Crenshaw (R-TX)
- **GOOGL** — 1 purchase · $250K–$500K · Rep. Pelosi (D-CA)

No notable sales above threshold this period.
```

---

## 8. Telegram Command: `/senate <TICKER>`

**Handler:** `TelegramDigestBot.senate()` in `src/finage/telegram_bot.py`  
**Analysis method:** `MomentumAnalysisService.senate(ticker)` in `src/finage/analysis.py`

### 8.1 Flow

```
User: /senate NVDA
  → normalize ticker (reuse existing normalize_ticker_symbol())
  → CongressCollector.get_or_fetch()   # cache hit if < 12h old
  → CongressCollector.trades_for_ticker(snapshot, "NVDA")  # reads by_ticker/NVDA.json
  → WsbCollector fetch_trending_tickers()  # cheap ApeWisdom call for current rank
  → build prompt from congress_ticker.md template
  → Gemini call
  → send_markdown_text() to user
```

### 8.2 Prompt Template (`src/finage/prompts/congress_ticker.md`)

```markdown
You are analyzing U.S. congressional trading disclosures for {ticker} ({asset_description}).

Congressional trade history (all available, sorted by disclosure date descending):
{trades_formatted}

Current social momentum context:
- ApeWisdom Reddit rank: {reddit_rank} (or "not in top {ticker_limit}" if absent)

Provide a concise analysis covering:
1. Overall pattern — are insiders buying or selling? Any cluster activity?
2. Largest trades and who made them.
3. Signal strength — how significant is this activity?
4. Whether the social momentum context strengthens or weakens the thesis.

If there are no trades, note that and provide general sector/regulatory context for {ticker}.
Keep the response under 400 words.
```

### 8.3 No-data Behavior

When `trades_for_ticker` returns an empty list, Gemini still receives the prompt with `{trades_formatted}` as `"No congressional trades found in local history."`. Gemini responds with sector/political context rather than a blank message. This matches the confirmed requirement.

### 8.4 Error Handling

- FMP network error during `/senate`: serve stale cache if available and note it; if no cache exists, reply "Congressional data unavailable — FMP could not be reached. Try again shortly."
- Invalid ticker (non-alpha, > 5 chars): reuse existing `normalize_ticker_symbol()` validation, same error message format as `/ticker`.

---

## 9. `MomentumAnalysisService.live()` Enrichment

The `/live` command's compact brief is lightly enriched: any ticker in the ApeWisdom top results that also has a `CongressSignal` above `min_score` gets a `🏛️` badge appended inline. No extra Gemini call — purely a data decoration on the existing brief string.

This is a low-effort addition that makes convergence visible without changing the core live-scan architecture.

---

## 10. New & Modified Files

### New

| File | Purpose |
|------|---------|
| `src/finage/congress.py` | `CongressCollector`, `CongressAnalyzer` |
| `src/finage/prompts/congress_digest.md` | Prompt fragment injected into daily digest |
| `src/finage/prompts/congress_ticker.md` | Prompt template for `/senate TICKER` |

### Modified

| File | Change |
|------|--------|
| `src/finage/models.py` | Add `CongressTrade`, `CongressSnapshot`, `CongressSignal` |
| `src/finage/settings.py` | Add `fmp_api_key`, `congress_*` settings |
| `src/finage/digest.py` | Gather congress data concurrently; inject into Gemini prompt |
| `src/finage/analysis.py` | Add `senate()` method; enrich `live()` with `🏛️` badges |
| `src/finage/telegram_bot.py` | Register `/senate` `CommandHandler`; add `senate()` handler |
| `.env.example` | Add `FMP_API_KEY`, `CONGRESS_LOOKBACK_DAYS`, `CONGRESS_CACHE_TTL_HOURS`, `CONGRESS_MIN_SIGNAL_SCORE` |

---

## 11. Settings Reference

| Env Var | Default | Description |
|---------|---------|-------------|
| `FMP_API_KEY` | *(required to enable)* | FMP API key; feature auto-disables if absent |
| `CONGRESS_LOOKBACK_DAYS` | `30` | Days of disclosures to include in digest signals |
| `CONGRESS_CACHE_TTL_HOURS` | `12` | Hours before cached snapshot is considered stale |
| `CONGRESS_MIN_SIGNAL_SCORE` | `2.0` | Minimum score for a ticker to appear in digest |

---

## 12. Out of Scope

- Politician party data (FMP latest endpoints do not return party affiliation; `is_bicameral` is used as a weaker proxy signal instead).
- Committee membership relevance scoring.
- Historical chart / trend tracking over multiple weeks.
- Push alerts when a new high-score trade appears mid-day (can be a future iteration).
