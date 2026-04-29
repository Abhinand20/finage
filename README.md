# Finage

Finage is a small personal financial analyst MVP. Phase 1 generates a ticker-first stock subreddit digest and delivers it through a restricted Telegram bot.

## What M1 Does

- Pulls currently trending WSB tickers from ApeWisdom.
- Scrapes recent posts and top comments from the configured stock subreddit list with Reddit API credentials.
- Keeps only evidence connected to the trending tickers.
- Uses Gemini API to write a concise Telegram-friendly digest.
- Saves the latest scrape and digest to local JSON files.
- Supports an on-demand `/digest` Telegram command and a CLI command suitable for cron or systemd timers.

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

The default stock subreddit list lives in `src/finage/settings.py` as `DEFAULT_STOCK_SUBREDDITS`. The default digest prompt lives at `src/finage/prompts/wsb_digest.md`. To experiment without editing package files, copy that file and set `DIGEST_PROMPT_PATH` to the copy. Custom prompt templates must include `{evidence_json}`, which is replaced with the scraped stock subreddit evidence.

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

`/live` runs an ad hoc social-momentum scan without overwriting the latest digest artifacts. `/ticker <stock>` runs a fresh scan and returns focused evidence for one ticker, including ApeWisdom rank context, qualifying Reddit posts, subreddit breadth, comments, and external links when available.

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
