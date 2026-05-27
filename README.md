# Finage

Finage is a small personal financial analyst MVP. It tracks social momentum around stocks, turns Reddit and market-context evidence into a concise Gemini digest, and delivers it through a restricted Telegram bot.

## What It Does Today

- Pulls currently trending tickers from ApeWisdom across the configured stock subreddit filters.
- Scrapes recent posts, top comments, subreddit breadth, and external links from the configured stock subreddit list with Reddit API credentials.
- Keeps only evidence connected to the trending tickers, then ranks it for digest and ticker-specific analysis.
- Uses Gemini API to write Telegram-friendly market briefs, ticker explanations, and full digests.
- Optionally enriches each digest with recent web context via [Exa](https://exa.ai) when `EXA_API_KEY` is set.
- Saves the latest scrape and digest to local JSON files so follow-up commands can compare against the most recent baseline.
- Supports on-demand Telegram commands plus CLI digest preview/send commands suitable for cron or systemd timers.

## Setup

Install dependencies with `uv`:

```bash
uv sync --extra dev
```

Create an environment file:

```bash
cp .env.example .env
```

Fill in:

- `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET` from a Reddit script app.
- `TELEGRAM_BOT_TOKEN` from BotFather.
- `TELEGRAM_ALLOWED_IDS` with your numeric Telegram user ID and/or chat ID.
- `TELEGRAM_DEFAULT_CHAT_ID` for scheduled sends.
- `GEMINI_API_KEY` for Gemini Developer API.
- Optional: `EXA_API_KEY` for per-ticker web search snippets in the digest prompt (see `.env.example` for related settings).

The default stock subreddit list lives in `src/finage/settings.py` as `DEFAULT_STOCK_SUBREDDITS`. Finage currently uses ApeWisdom trend filters for those subreddit names, then scans Reddit for matching evidence.

The default digest prompt is the bundled file `wsb_digest.md` under `src/finage/prompts/`. Set `DIGEST_PROMPT_BUNDLE` in the environment, for example `wsb_digest_new.md`, to pick another bundled template. To use an arbitrary file on disk, set `DIGEST_PROMPT_PATH`; that overrides the bundle. Custom prompt templates must include `{evidence_json}`, which is replaced with the scraped stock subreddit evidence.

### Optional Web Search

Digest web search is disabled unless Exa is configured. Setting `EXA_API_KEY` automatically selects Exa and enables digest enrichment unless `DIGEST_WEB_SEARCH_ENABLED=false` is also set.

Useful knobs:

- `WEB_SEARCH_NUM_RESULTS`: number of Exa results per ticker, default `3`.
- `WEB_SEARCH_CONTENT_MODE`: `highlights`, `text`, or `none`.
- `WEB_SEARCH_TIMEOUT_SECONDS`: request timeout, default `15`.
- `DIGEST_WEB_SEARCH_ENABLED`: explicit on/off switch for digest enrichment.

## Usage

Preview a digest locally without sending it:

```bash
uv run finage digest preview
```

Generate and send one digest to `TELEGRAM_DEFAULT_CHAT_ID`:

```bash
uv run finage digest send
```

Run the Telegram bot listener:

```bash
uv run finage bot
```

Available bot commands:

- `/start`
- `/help`
- `/digest`
- `/live`
- `/ticker <stock>`
- `/why <stock>`
- `/movers`
- `/health`

`/digest` generates and returns a full digest, overwriting the latest snapshot and digest artifacts. `/live` runs an ad hoc social-momentum scan without overwriting artifacts. `/ticker <stock>` runs a fresh scan and returns focused evidence for one ticker, including ApeWisdom rank context, qualifying Reddit posts, subreddit breadth, comments, and external links when available. `/why <stock>` runs a fresh scan and asks Gemini for a concise explanation of the ticker's narrative, sentiment, catalysts, evidence strength, and uncertainty. `/movers` runs a fresh scan and compares it against the latest saved digest snapshot without overwriting artifacts. `/health` checks configuration, latest artifacts, the data directory, and ApeWisdom reachability.

The bot rejects requests unless the effective Telegram user ID or chat ID is listed in `TELEGRAM_ALLOWED_IDS`.

## Raspberry Pi Deployment (PM2)

This setup runs Finage under PM2 for crash recovery and reboot persistence.

1. Clone the repo on the Pi at `/home/alphacode/finage`.
2. Install `uv`, Node.js, and npm.
3. Install project dependencies and set up `.env`:

```bash
cd /home/alphacode/finage
uv sync
cp .env.example .env
```

4. Install PM2 globally:

```bash
sudo npm install -g pm2
```

5. Start Finage with the included PM2 ecosystem file:

```bash
cd /home/alphacode/finage
pm2 start ecosystem.config.cjs
```

This starts:

- `finage-bot`: always-on Telegram bot (`uv run finage bot`).
- `finage-digest`: weekday 07:00 scheduled digest sender (`uv run finage digest send`).

6. Save PM2 process state and enable startup on boot:

```bash
pm2 save
pm2 startup systemd -u alphacode --hp /home/alphacode
```

Run the `sudo ...` command printed by `pm2 startup`, then run `pm2 save` again.

### PM2 Verify and Operations

Check process status:

```bash
pm2 status
```

View logs:

```bash
pm2 logs finage-bot
pm2 logs finage-digest
```

Pull the latest `main` branch and restart the bot:

```bash
scripts/deploy-latest.sh
```

Run a one-off manual digest send:

```bash
cd /home/alphacode/finage
uv run finage digest send
```

After reboot, confirm both processes are registered:

```bash
pm2 status
```

## Latest Artifacts

By default, Finage writes:

- `data/latest_wsb_snapshot.json`
- `data/latest_digest.json`

These are latest-run files only. M1 does not maintain historical storage.
