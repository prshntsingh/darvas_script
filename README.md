# darvas_script: Telegram to Email & Notion Forwarder

A Python-based Telegram UserBot script that listens to specific channels and automatically forwards new messages to an Email address and logs them into a Notion page.

## Features
- **Live Forwarding**: Instantly forwards incoming text and media captions.
- **Notion Integration**: Appends messages to a Notion page with timestamps.
- **Email Integration**: Sends SMTP emails with the message content.
- **Discord Integration**: Forwards messages to Discord channels via webhooks.
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
   VERTEX_LOCATION=us-central1
   GOOGLE_SHEET_ID=your_google_sheet_id
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

> **Backward compatible**: If `CHANNEL_MAPPINGS` is not set, the bot automatically uses the legacy `TG_TARGET_CHANNEL_ID` + `DISCORD_WEBHOOK_URL` as a single mapping. No changes needed for existing setups.

**Shared services**: Notion, Google Sheets, and AI trade extraction remain shared across all channel mappings — they use the same global configuration.

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
