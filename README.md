# darvas_script: Telegram to Email & Notion Forwarder

A Python-based Telegram UserBot script that listens to specific channels and automatically forwards new messages to an Email address and logs them into a Notion page.

## Features
- **Live Forwarding**: Instantly forwards incoming text and media captions.
- **Notion Integration**: Appends messages to a Notion page with timestamps.
- **Email Integration**: Sends SMTP emails with the message content.
- **Checkpointing (Catch-up)**: If the script goes offline, it automatically remembers where it left off and catches up on any missed messages when restarted.
- **Daily Notion Dividers**: Automatically inserts a visual dividing line in Notion at the start of a new day (in IST Timezone) for easy reading.
- **Multi-Account Support**: Supports running multiple Telegram accounts simultaneously in the same directory using different `.env` configuration files.
- **Cloud-Ready**: Uses Telethon `StringSession` to easily run on cloud hosts like Koyeb without needing manual phone authentication every time.

## Prerequisites
1. **Telegram API ID and Hash**: Get these from [my.telegram.org](https://my.telegram.org)
2. **Notion API Key & Page ID**: Create an integration at [Notion Developers](https://www.notion.so/my-integrations) and share the target page with your integration.
3. **Gmail App Password**: For sending emails. Generate one in your Google Account Security settings.

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
   ```

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
python telegram_to_email.py
```

**Running Multiple Accounts:**
To run a second account, create a copy of your `.env` file (e.g., `.env2`), update the `TG_SESSION_STRING` and `TG_TARGET_CHANNEL_ID`, and run it in a separate terminal:
```bash
python telegram_to_email.py .env2
```