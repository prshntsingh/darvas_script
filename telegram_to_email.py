import os
import asyncio
import smtplib
import requests
from datetime import datetime
from email.mime.text import MIMEText
from telethon import TelegramClient, events
from dotenv import load_dotenv

load_dotenv()

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

def send_to_notion_sync(text):
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
    
    # Add date/time prefix as requested
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_text = f"[{current_time}] {text}"
    
    data = {
        "children": [
            {
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
            }
        ]
    }
    
    try:
        response = requests.patch(url, headers=headers, json=data)
        response.raise_for_status()
        print("Successfully logged message to Notion.")
    except Exception as e:
        print(f"Failed to log to Notion: {e}")
        if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
            print(f"Notion API Error: {e.response.text}")

async def send_to_notion_async(text):
    """Asynchronously run the blocking Notion API function."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, send_to_notion_sync, text)

async def main():
    if not API_ID or not API_HASH or not TARGET_CHANNEL_ID:
        print("Error: Telegram API configuration is incomplete.")
        print("Please set TG_API_ID, TG_API_HASH, and TG_TARGET_CHANNEL_ID environment variables.")
        return

    # Initialize the client. It will prompt for phone/code on first run
    # and save the session to userbot_session.session
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

    @client.on(events.NewMessage(chats=TARGET_CHANNEL_ID))
    async def handler(event):
        message = event.message
        
        # Extract the text of the message, or note if it is media without a caption
        if message.text:
            text = message.text
        elif message.media:
            text = "[Media received without caption]"
        else:
            text = "[Empty or unknown message format]"
            
        print(f"New message received from channel {TARGET_CHANNEL_ID}:\n{text}")
        print("-" * 40)
        
        subject = f"New Telegram Message from Channel {TARGET_CHANNEL_ID}"
        
        # Dispatch the email sending task as a background task.
        # This allows the event handler to return immediately, keeping the client responsive.
        # asyncio.create_task(send_email_async(subject, text))
        
        # Dispatch the Notion logging task as a background task.
        asyncio.create_task(send_to_notion_async(text))

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
    
    print(f"Successfully connected! Listening for new messages in channel: {TARGET_CHANNEL_ID}...")
    # Run the client until it's disconnected (Ctrl+C)
    await client.run_until_disconnected()

if __name__ == '__main__':
    # Run the main async loop
    asyncio.run(main())
