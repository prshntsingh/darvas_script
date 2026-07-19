import os
import asyncio
import zoneinfo
from telethon import TelegramClient, events

from config import (
    API_ID, 
    API_HASH, 
    TARGET_CHANNEL_ID, 
    SESSION_STRING, 
    SESSION_NAME
)
from state_manager import (
    read_checkpoint, 
    write_checkpoint, 
    read_last_date, 
    write_last_date
)
from services.notion_service import send_to_notion_async
from services.discord_service import send_to_discord_async
from services.gemini_service import extract_trade_async
from services.google_sheets_service import append_to_sheet_async

async def main():
    if not API_ID or not API_HASH or not TARGET_CHANNEL_ID:
        print("Error: Telegram API configuration is incomplete.")
        print("Please set TG_API_ID, TG_API_HASH, and TG_TARGET_CHANNEL_ID environment variables.")
        return

    if SESSION_STRING:
        from telethon.sessions import StringSession
        client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    else:
        client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

    async def process_telegram_message(message, channel_id):
        media_path = None
        if message.media:
            # Create a temp directory if it doesn't exist
            if not os.path.exists("temp_media"):
                os.makedirs("temp_media")
            try:
                # Download media
                print(f"Downloading media for message {message.id}...")
                media_path = await message.download_media(file="temp_media/")
                print(f"Downloaded media to: {media_path}")
            except Exception as e:
                print(f"Failed to download media: {e}")
                
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
        
        # Dispatch the Notion logging task as a background task.
        asyncio.create_task(send_to_notion_async(text, message.date, is_new_day))
        
        # Dispatch the Discord logging task as a background task.
        asyncio.create_task(send_to_discord_async(text, media_path))
        
        # AI Trade Extraction
        trade_data = await extract_trade_async(text)
        if trade_data:
            formatted_time = message_date_ist.strftime("%Y-%m-%d %H:%M:%S")
            asyncio.create_task(append_to_sheet_async(trade_data, formatted_time))
        
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
        latest = await client.get_messages(TARGET_CHANNEL_ID, limit=1)
        if latest:
            write_checkpoint(TARGET_CHANNEL_ID, latest[0].id)
            print(f"Created initial checkpoint at message ID {latest[0].id}.")

    print(f"Listening for new live messages in channel: {TARGET_CHANNEL_ID}...")
    await client.run_until_disconnected()

if __name__ == '__main__':
    # Run the main async loop
    asyncio.run(main())
