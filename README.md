# darvas_script: Telegram Signal Broadcaster & Trading Bots

A Telegram UserBot that listens to trading channels and does two things:
- **Forwards and logs** every message to Notion, Discord, Email and Google Sheets.
- **Broadcasts trade signals** over a WebSocket to two trading bots that place orders on Dhan (or Kite):
  - the **equity bot** (`client_agent/`)
  - the **options (FnO) bot** (`fno_agent/`)

```
Telegram ──► Broadcaster (main.py, Railway) ──wss──► Equity bot  (client_agent/, GCP VM) ──► Dhan
                                            └─wss──► Options bot (fno_agent/,    GCP VM) ──► Dhan / Kite
```

## Trading bots at a glance

| | Equity bot | Options (FnO) bot |
|---|---|---|
| Code | [`client_agent/`](client_agent/README.md) | [`fno_agent/`](fno_agent/README.md) |
| Deploy guide | [`client_agent/DEPLOY.md`](client_agent/DEPLOY.md) | [`fno_agent/DEPLOY.md`](fno_agent/DEPLOY.md) |
| Signals from | Channels with `"enable_trading": true` | Channels with `"enable_fno_trading": true` |
| Example signal | `Buy #SBIN @ 805-810` | `#POLYCAB 8000 PE OCT @90-105 / SL-30 / Target-500,1000` |
| Sizing | `TRADE_AMOUNT_INR` ÷ price | Whole lots that fit `FNO_CAPITAL_PER_TRADE` |
| Stop-loss / target | **Optional**, off by default: `EQUITY_SL_TARGET_ENABLED` places fixed-% SL (`EQUITY_SL_PCT`) and target (`EQUITY_TARGET_PCT`, default 1%) as a Dhan super order | **Optional**, on by default: `FNO_SL_TARGET_ENABLED` places the signal's SL/targets at the broker |
| Setup on the VM | `bash client_agent/deploy/setup.sh` | `bash fno_agent/deploy/setup.sh` |

On the VM, both bots are controlled with one command: `bot status`, `bot logs`, `bot today`, `bot live equity|fno`, `bot test equity|fno`, `bot settings equity|fno`, `bot restart`, `bot update`. Run `bot help` for the full list. Both bots start in TEST mode (`DRY_RUN=true`) and share one Dhan login token (`~/.dhan_token`).

## Features
- **Live Forwarding**: Instantly forwards incoming text and media captions.
- **Notion Integration**: Appends messages to a Notion page with timestamps.
- **Email Integration**: Sends SMTP emails with the message content.
- **Discord Integration**: Forwards messages to Discord channels via webhooks.
- **Trade Signal Broadcasting**: Parses equity and option calls, using regex first and Gemini as a fallback. It also resolves company names to tickers (e.g. "MANIPAL PAYMENT" → `MPIMANIPAL`). Signals are pushed to the trading bots over an authenticated WebSocket.
- **AI Trade Extraction**: Uses Vertex AI (Gemini) to detect trade setups and log them to Google Sheets with auto-calculated targets, stop losses, and live price tracking.
- **Multiple Channel Mappings**: Map multiple Telegram channels to individual Discord webhooks within a single bot process — no need to run separate instances.
- **Checkpointing (Catch-up)**: If the script goes offline, it automatically remembers where it left off and catches up on any missed messages when restarted.
- **Daily Notion Dividers**: Automatically inserts a visual dividing line in Notion at the start of a new day (in IST Timezone) for easy reading.
- **Multi-Account Support**: Supports running multiple Telegram accounts simultaneously in the same directory using different `.env` configuration files.
- **Cloud-Ready**: Uses Telethon `StringSession` to easily run on cloud hosts like Koyeb without needing manual phone authentication every time.

## Prerequisites
1. **Telegram API ID and Hash**: Get these from [my.telegram.org](https://my.telegram.org)
2. **Notion API Key & Page ID**: Create an integration at [Notion Developers](https://www.notion.so/my-integrations) and share the target page with your integration.
3. **Gmail App Password**: For sending emails. Generate one in your Google Account Security settings.
4. **Discord Webhook URL** *(optional)*: Create a webhook in your Discord channel's Integrations settings.
5. **Google Cloud Project** *(optional)*: For Vertex AI trade extraction and Google Sheets logging.

## Setup
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Create a `.env` file (or copy `.env.example` if available) and fill in your credentials:
   ```env
   TG_API_ID=your_api_id
   TG_API_HASH=your_api_hash
   TG_TARGET_CHANNEL_ID=-100XXXXXXXXXX
   
   SMTP_SERVER=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USERNAME=your_email@gmail.com
   SMTP_PASSWORD=your_app_password
   EMAIL_FROM=your_email@gmail.com
   EMAIL_TO=recipient_email@gmail.com
   
   NOTION_API_KEY=your_notion_secret
   NOTION_PAGE_ID=your_notion_page_id
   
   DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
   
   VERTEX_PROJECT_ID=your_gcp_project_id
   GEMINI_LOCATION=global          # Gemini endpoint (default: global)
   GOOGLE_SHEET_ID=your_google_sheet_id

   WS_AUTH_TOKEN=a_long_random_password   # the trading bots connect with this
   FNO_MAX_SIGNAL_AGE_SEC=120             # option signals older than this are never broadcast
   ```

## Multiple Channel Mappings

You can map multiple Telegram channels to different Discord webhooks within a single bot process. Add a `CHANNEL_MAPPINGS` environment variable as a JSON array:

```env
CHANNEL_MAPPINGS=[{"telegram_channel_id": -100111, "discord_webhook_url": "https://discord.com/api/webhooks/...", "label": "channel-a"}, {"telegram_channel_id": -100222, "discord_webhook_url": "https://discord.com/api/webhooks/...", "label": "channel-b"}]
```

Each mapping object supports these fields:

| Field | Required | Description |
|---|---|---|
| `telegram_channel_id` | ✅ | The Telegram channel ID to listen to |
| `discord_webhook_url` | ❌ | Discord webhook URL for this channel (omit to skip Discord) |
| `label` | ❌ | A friendly name for logs (defaults to the channel ID) |
| `enable_trading` | ❌ | `true` = broadcast **equity** signals from this channel to the equity bot |
| `enable_fno_trading` | ❌ | `true` = broadcast **option** signals from this channel to the FnO bot |

> **Backward compatible**: If `CHANNEL_MAPPINGS` is not set, the bot automatically uses the legacy `TG_TARGET_CHANNEL_ID` + `DISCORD_WEBHOOK_URL` as a single mapping. No changes needed for existing setups.

**Shared services**: Notion, Google Sheets, and AI trade extraction remain shared across all channel mappings — they use the same global configuration.

## Deploying the trading setup

- **Broadcaster (Railway):** see Part 1 of [`client_agent/DEPLOY.md`](client_agent/DEPLOY.md) (equity server) and [`fno_agent/DEPLOY.md`](fno_agent/DEPLOY.md) (options server).
  - Each Railway broadcaster needs its own `WS_AUTH_TOKEN` and its own `TG_SESSION_STRING`.
- **Bots (GCP VM):** see Part 2 of the same guides. Each bot is one command with guided questions.

## Cloud Deployment (Optional)
If you want to host this on a cloud provider like Koyeb, you need a String Session instead of a local SQLite session file.
1. Run the session generator:
   ```bash
   python generate_string_session.py
   ```
2. Authenticate with your phone number.
3. Add the outputted string to your `.env` file or cloud environment variables under the key `TG_SESSION_STRING`.

## Usage
Run the script locally using your default `.env` file:
```bash
python main.py
```

**Running Multiple Accounts:**
To run a second account, create a copy of your `.env` file (e.g., `.env2`), update the `TG_SESSION_STRING` and channel configuration, and run it in a separate terminal:
```bash
python main.py .env2
```

## Testing
Run the test suite:
```bash
pytest tests/ -v
```
