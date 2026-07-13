import os
import asyncio
import smtplib
import requests
from datetime import datetime
import zoneinfo
from email.mime.text import MIMEText
from telethon import TelegramClient, events
from dotenv import load_dotenv
import sys

env_file = sys.argv[1] if len(sys.argv) > 1 else '.env'
load_dotenv(env_file, override=True)
print(f"Loaded configuration from {env_file}")

# ==========================================
# Configuration
# ==========================================
# Telegram API Credentials (get from my.telegram.org)
API_ID_STR = os.environ.get('TG_API_ID', '')
API_ID = int(API_ID_STR) if API_ID_STR else 0
API_HASH = os.environ.get('TG_API_HASH', '')

# The channel ID to listen to. Needs to be an integer.
# Note: In Telethon, channel IDs often start with -100 (e.g., -100123456789)
TARGET_CHANNEL_ID_STR = os.environ.get('TG_TARGET_CHANNEL_ID', '')
TARGET_CHANNEL_ID = int(TARGET_CHANNEL_ID_STR) if TARGET_CHANNEL_ID_STR else 0

# Email Configuration
SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT_STR = os.environ.get('SMTP_PORT', '587')
SMTP_PORT = int(SMTP_PORT_STR) if SMTP_PORT_STR else 587
SMTP_USERNAME = os.environ.get('SMTP_USERNAME', '')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '') # Use an App Password for Gmail
EMAIL_FROM = os.environ.get('EMAIL_FROM', SMTP_USERNAME)
EMAIL_TO = os.environ.get('EMAIL_TO', '')

# Notion Configuration
NOTION_API_KEY = os.environ.get('NOTION_API_KEY', '')
NOTION_PAGE_ID = os.environ.get('NOTION_PAGE_ID', '')

# Session file name (will be saved as userbot_session.session)
SESSION_NAME = 'userbot_session'

def send_email_sync(subject, body):
    """Synchronous function to send an email using smtplib."""
    if not all([SMTP_USERNAME, SMTP_PASSWORD, EMAIL_TO]):
        print("Email configuration is incomplete. Skipping email send.")
        return

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = EMAIL_FROM
    msg['To'] = EMAIL_TO

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
        print(f"Successfully sent email: {subject}")
    except Exception as e:
        print(f"Failed to send email: {e}")

async def send_email_async(subject, body):
    """
    Asynchronously run the blocking email function.
    This ensures the Telethon event loop is not blocked by the SMTP network call.
    """
    loop = asyncio.get_running_loop()
    # Run the blocking synchronous function in the default executor (thread pool)
    await loop.run_in_executor(None, send_email_sync, subject, body)

def send_to_notion_sync(text, message_date_utc, is_new_day):
    """Synchronous function to append a message block to a Notion page."""
    if not all([NOTION_API_KEY, NOTION_PAGE_ID]):
        print("Notion configuration is incomplete. Skipping Notion logging.")
        return

    url = f"https://api.notion.com/v1/blocks/{NOTION_PAGE_ID}/children"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }
    
    # Use IST timezone
    ist_zone = zoneinfo.ZoneInfo("Asia/Kolkata")
    message_date_ist = message_date_utc.astimezone(ist_zone)
    formatted_time = message_date_ist.strftime("%Y-%m-%d %H:%M:%S")
    formatted_text = f"[{formatted_time}] {text}"
    
    children = []
    if is_new_day:
        children.append({
            "object": "block",
            "type": "divider",
            "divider": {}
        })
        
    children.append({
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [
                {
                    "type": "text",
                    "text": {
                        "content": formatted_text
                    }
                }
            ]
        }
    })
    
    data = { "children": children }
    
    try:
        response = requests.patch(url, headers=headers, json=data)
        response.raise_for_status()
        print("Successfully logged message to Notion.")
    except Exception as e:
        print(f"Failed to log to Notion: {e}")
        if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
            print(f"Notion API Error: {e.response.text}")

async def send_to_notion_async(text, message_date_utc, is_new_day):
    """Asynchronously run the blocking Notion API function."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, send_to_notion_sync, text, message_date_utc, is_new_day)

async def main():
    if not API_ID or not API_HASH or not TARGET_CHANNEL_ID:
        print("Error: Telegram API configuration is incomplete.")
        print("Please set TG_API_ID, TG_API_HASH, and TG_TARGET_CHANNEL_ID environment variables.")
        return

    # Check if we are running in the cloud with a StringSession
    session_string = os.environ.get('TG_SESSION_STRING', '')
    
    if session_string:
        from telethon.sessions import StringSession
        client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
    else:
        # Initialize the client. It will prompt for phone/code on first run locally
        # and save the session to userbot_session.session
        client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

    def get_checkpoint_file(channel_id):
        return f".checkpoint_{channel_id}.txt"

    def read_checkpoint(channel_id):
        file_path = get_checkpoint_file(channel_id)
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r') as f:
                    return int(f.read().strip())
            except Exception as e:
                print(f"Error reading checkpoint: {e}")
        return None

    def write_checkpoint(channel_id, message_id):
        file_path = get_checkpoint_file(channel_id)
        try:
            with open(file_path, 'w') as f:
                f.write(str(message_id))
        except Exception as e:
            print(f"Error writing checkpoint: {e}")

    def read_last_date(channel_id):
        file_path = f".last_date_{channel_id}.txt"
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r') as f:
                    return f.read().strip()
            except Exception as e:
                print(f"Error reading last date: {e}")
        return None

    def write_last_date(channel_id, date_str):
        try:
            with open(f".last_date_{channel_id}.txt", 'w') as f:
                f.write(date_str)
        except Exception as e:
            print(f"Error writing last date: {e}")

    async def process_telegram_message(message, channel_id):
        # Extract the text of the message, or note if it is media without a caption
        if message.text:
            text = message.text
        elif message.media:
            text = "[Media received without caption]"
        else:
            text = "[Empty or unknown message format]"
            
        print(f"Processing message {message.id} from channel {channel_id}:\n{text}")
        print("-" * 40)
        
        # Check if it's a new day in IST
        ist_zone = zoneinfo.ZoneInfo("Asia/Kolkata")
        message_date_ist = message.date.astimezone(ist_zone)
        msg_date_str = message_date_ist.strftime("%Y-%m-%d")
        
        last_date_str = read_last_date(channel_id)
        is_new_day = False
        if last_date_str != msg_date_str:
            is_new_day = True
            write_last_date(channel_id, msg_date_str)
        
        subject = f"New Telegram Message from Channel {channel_id}"
        
        # Dispatch the email sending task as a background task.
        # asyncio.create_task(send_email_async(subject, text))
        
        # Dispatch the Notion logging task as a background task.
        asyncio.create_task(send_to_notion_async(text, message.date, is_new_day))
        
        # Update checkpoint
        write_checkpoint(channel_id, message.id)

    @client.on(events.NewMessage(chats=TARGET_CHANNEL_ID))
    async def handler(event):
        await process_telegram_message(event.message, TARGET_CHANNEL_ID)


    @client.on(events.NewMessage())
    async def debug_handler(event):
        # Print the chat ID and message of ANY incoming message
        chat_id = event.chat_id
        text = event.message.message or "[No text/Media]"
        
        if chat_id != TARGET_CHANNEL_ID:
            print(f"[DEBUG] Received a message from chat ID: {chat_id}")
            print(f"[DEBUG] Message Content: {text}")
            print(f"[DEBUG] If this is your channel, update TARGET_CHANNEL_ID to {chat_id}!")
            print("-" * 40)

    print("Starting Telegram UserBot...")
    await client.start()
    
    print(f"Successfully connected! Checking for missed messages in channel: {TARGET_CHANNEL_ID}...")
    
    # Catch-up phase
    last_id = read_checkpoint(TARGET_CHANNEL_ID)
    if last_id is not None:
        print(f"Found checkpoint! Fetching missed messages after ID {last_id}...")
        missed_messages = []
        async for message in client.iter_messages(TARGET_CHANNEL_ID, min_id=last_id, reverse=True):
            missed_messages.append(message)
        
        if missed_messages:
            print(f"Found {len(missed_messages)} missed messages. Catching up...")
            for msg in missed_messages:
                await process_telegram_message(msg, TARGET_CHANNEL_ID)
            print("Catch-up complete!")
        else:
            print("No missed messages found.")
    else:
        print("No checkpoint found. Starting fresh.")
        # If no checkpoint exists, let's create one with the latest message to avoid fetching history from the beginning of time if restarted.
        # We fetch just the 1 latest message.
        latest = await client.get_messages(TARGET_CHANNEL_ID, limit=1)
        if latest:
            write_checkpoint(TARGET_CHANNEL_ID, latest[0].id)
            print(f"Created initial checkpoint at message ID {latest[0].id}.")

    print(f"Listening for new live messages in channel: {TARGET_CHANNEL_ID}...")
    # Run the client until it's disconnected (Ctrl+C)
    await client.run_until_disconnected()

if __name__ == '__main__':
    # Run the main async loop
    asyncio.run(main())
