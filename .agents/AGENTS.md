# AI Agent Instructions for `darvas_script`

Welcome, fellow agent! If you are working on this repository, please review these architectural details and rules to avoid breaking the delicate Telegram and Notion integrations.

## Architecture Context
- **Core Library**: The script uses `telethon` to run as a Telegram UserBot (not a Bot API bot).
- **Asynchronous Execution**: The `telethon` event loop (`client.run_until_disconnected()`) is blocking. Heavy external I/O (like SMTP emails and Notion API patches) MUST be dispatched using `asyncio.create_task()` calling `loop.run_in_executor()` wrappers. Do not block the `events.NewMessage` handler.
- **Timezone Requirements**: The user strictly requested that all timestamps sent to Notion be formatted in **Indian Standard Time (IST / Asia/Kolkata)**. This handles discrepancies when deployed to UTC-based cloud platforms (e.g., Koyeb).

## Checkpointing and State Management
The script has a custom offline-recovery mechanism:
1. **Missed Messages**: Before entering the live listening loop, it fetches missed historical messages using `client.iter_messages` based on a saved checkpoint.
2. **State Files**:
   - `.checkpoint_<CHANNEL_ID>.txt`: Stores the integer `message.id` of the last processed message.
   - `.last_date_<CHANNEL_ID>.txt`: Stores the IST date string (e.g., `2026-07-08`) of the last processed message. This is used to append a `"divider"` block in Notion on a new day.
3. **Important**: Since local storage might be ephemeral on free cloud tiers (Koyeb), the script is designed to tolerate missing state files by starting fresh with the *latest* single message if no checkpoint is found.

## Environment Variables & Multiple Instances
- The script uses `python-dotenv`.
- It dynamically loads configuration files passed via command-line arguments (e.g., `python main.py .env2`).
- We use `load_dotenv(env_file, override=True)` to ensure multiple terminals running different `.env` files don't leak environment variables to each other if cached or exported globally.

## Modifying Logic
When editing `main.py`:
- Do not remove `zoneinfo.ZoneInfo("Asia/Kolkata")`.
- Keep the `Notion-Version: 2022-06-28` header intact.
- Media forwarding is currently logged as text `[Media received without caption]`. If you implement actual media downloading in the future, ensure it downloads to a temporary path, attaches to the email as `MIMEMultipart`, and then deletes the temporary file to preserve container disk space.
