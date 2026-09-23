import os
import sys
import json
from dataclasses import dataclass
from dotenv import load_dotenv

# Parse sys.argv for environment file
env_file = sys.argv[1] if len(sys.argv) > 1 else '.env'
load_dotenv(env_file, override=True)
print(f"Loaded configuration from {env_file}")

# Telegram API Credentials
API_ID_STR = os.environ.get('TG_API_ID', '')
API_ID = int(API_ID_STR) if API_ID_STR else 0
API_HASH = os.environ.get('TG_API_HASH', '')

# Target Channel (legacy single-channel config, kept for backward compatibility)
TARGET_CHANNEL_ID_STR = os.environ.get('TG_TARGET_CHANNEL_ID', '')
TARGET_CHANNEL_ID = int(TARGET_CHANNEL_ID_STR) if TARGET_CHANNEL_ID_STR else 0

# String Session
SESSION_STRING = os.environ.get('TG_SESSION_STRING', '')
SESSION_NAME = 'userbot_session'

# Email Configuration
SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT_STR = os.environ.get('SMTP_PORT', '587')
SMTP_PORT = int(SMTP_PORT_STR) if SMTP_PORT_STR else 587
SMTP_USERNAME = os.environ.get('SMTP_USERNAME', '')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
EMAIL_FROM = os.environ.get('EMAIL_FROM', SMTP_USERNAME)
EMAIL_TO = os.environ.get('EMAIL_TO', '')

# Notion Configuration
NOTION_API_KEY = os.environ.get('NOTION_API_KEY', '')
NOTION_PAGE_ID = os.environ.get('NOTION_PAGE_ID', '')

# Discord Configuration (legacy single-webhook, kept for backward compatibility)
DISCORD_WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK_URL', '')

# Vertex AI Configuration
VERTEX_PROJECT_ID = os.environ.get('VERTEX_PROJECT_ID', '')
VERTEX_LOCATION = os.environ.get('VERTEX_LOCATION', 'us-central1')  # legacy; Gemini uses GEMINI_LOCATION
# Gemini endpoint. 'global' serves every current Flash model (incl. the gemini-3.x fallback, which
# 404s in us-central1) and routes to available capacity. Override only if you need data residency.
GEMINI_LOCATION = os.environ.get('GEMINI_LOCATION', 'global')

# Google Sheets Configuration
GOOGLE_SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '')

# Telegram Trade Log Channel (sends trade execution logs to a Telegram group)
LOG_GROUP_ID_STR = os.environ.get('LOG_GROUP_ID', '')
LOG_GROUP_ID = int(LOG_GROUP_ID_STR) if LOG_GROUP_ID_STR else 0

# Inject Google Cloud Credentials for Vertex AI & Google Sheets
# Only set if the file exists AND is non-empty (an empty file causes SDK errors)
if os.path.exists('credentials.json') and os.path.getsize('credentials.json') > 0:
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = 'credentials.json'

# --- Multiple Channel Mappings ---
# Each mapping defines a 1:1 Telegram channel → Discord webhook pair.
# Set CHANNEL_MAPPINGS as a JSON array in your .env file, e.g.:
#   CHANNEL_MAPPINGS=[{"telegram_channel_id": -100123, "discord_webhook_url": "https://...", "label": "my-channel"}]
# If not set, falls back to the legacy TG_TARGET_CHANNEL_ID + DISCORD_WEBHOOK_URL.

@dataclass
class ChannelMapping:
    telegram_channel_id: int
    discord_webhook_url: str
    label: str
    enable_trading: bool = False  # Opt-in: only broadcast trade signals for this channel
    enable_fno_trading: bool = False  # Opt-in: parse & broadcast option (FnO) signals for this channel

def _parse_channel_mappings():
    """Parse CHANNEL_MAPPINGS from env, or fall back to legacy single-channel vars."""
    raw = os.environ.get('CHANNEL_MAPPINGS', '').strip()
    
    if raw:
        try:
            entries = json.loads(raw)
            if not isinstance(entries, list) or len(entries) == 0:
                raise ValueError("CHANNEL_MAPPINGS must be a non-empty JSON array")
            
            mappings = []
            for entry in entries:
                tg_id = int(entry['telegram_channel_id'])
                webhook = entry.get('discord_webhook_url', '')
                label = entry.get('label', str(tg_id))
                enable_trading = entry.get('enable_trading', False)
                enable_fno_trading = entry.get('enable_fno_trading', False)
                mappings.append(ChannelMapping(
                    telegram_channel_id=tg_id,
                    discord_webhook_url=webhook,
                    label=label,
                    enable_trading=bool(enable_trading),
                    enable_fno_trading=bool(enable_fno_trading),
                ))
            return mappings
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            print(f"Warning: Failed to parse CHANNEL_MAPPINGS: {e}")
            print("Falling back to legacy single-channel configuration.")
    
    # Fallback: build a single mapping from the legacy env vars
    if TARGET_CHANNEL_ID:
        return [ChannelMapping(
            telegram_channel_id=TARGET_CHANNEL_ID,
            discord_webhook_url=DISCORD_WEBHOOK_URL,
            label='default'
        )]
    
    return []

CHANNEL_MAPPINGS = _parse_channel_mappings()
# O(1) lookup: telegram_channel_id → ChannelMapping
CHANNEL_MAP = {m.telegram_channel_id: m for m in CHANNEL_MAPPINGS}
