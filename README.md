# Finage

Finage is a small personal financial analyst MVP. Phase 1 generates a ticker-first WallStreetBets digest and delivers it through a restricted Telegram bot.

## What M1 Does

- Pulls currently trending WSB tickers from ApeWisdom.
- Scrapes recent `r/wallstreetbets` posts and top comments with Reddit API credentials.
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

The default digest prompt lives at `src/finage/prompts/wsb_digest.md`. To experiment without editing package files, copy that file and set `DIGEST_PROMPT_PATH` to the copy. Custom prompt templates must include `{evidence_json}`, which is replaced with the scraped WSB evidence.

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

The bot rejects requests unless the effective Telegram user ID or chat ID is listed in `TELEGRAM_ALLOWED_IDS`.

## Raspberry Pi Deployment

Clone the repo on the Pi, install `uv`, run `uv sync`, and create `.env` in the repo root.

For the long-running bot, create `/etc/systemd/system/finage-bot.service`:

```ini
[Unit]
Description=Finage Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/pi/finage
EnvironmentFile=/home/pi/finage/.env
ExecStart=/usr/local/bin/uv run finage bot
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now finage-bot.service
```

For scheduled daily sends, use cron:

```cron
0 7 * * 1-5 cd /home/pi/finage && /usr/local/bin/uv run finage digest send >> /home/pi/finage/data/cron.log 2>&1
```

Or create a systemd oneshot service `/etc/systemd/system/finage-digest.service`:

```ini
[Unit]
Description=Send Finage WSB digest
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/home/pi/finage
EnvironmentFile=/home/pi/finage/.env
ExecStart=/usr/local/bin/uv run finage digest send
```

And a timer `/etc/systemd/system/finage-digest.timer`:

```ini
[Unit]
Description=Run Finage WSB digest each weekday morning

[Timer]
OnCalendar=Mon..Fri 07:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable the timer:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now finage-digest.timer
```

## Latest Artifacts

By default, Finage writes:

- `data/latest_wsb_snapshot.json`
- `data/latest_digest.json`

These are latest-run files only. M1 does not maintain historical storage.
