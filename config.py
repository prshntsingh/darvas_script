import os
import sys
from dotenv import load_dotenv

# Parse sys.argv for environment file
env_file = sys.argv[1] if len(sys.argv) > 1 else '.env'
load_dotenv(env_file, override=True)
print(f"Loaded configuration from {env_file}")

# Telegram API Credentials
API_ID_STR = os.environ.get('TG_API_ID', '')
API_ID = int(API_ID_STR) if API_ID_STR else 0
API_HASH = os.environ.get('TG_API_HASH', '')

# Target Channel
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

# Discord Configuration
DISCORD_WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK_URL', '')
