import os
import asyncio
import zoneinfo
from telethon import TelegramClient, events

from config import (
    API_ID, 
    API_HASH, 
    SESSION_STRING, 
    SESSION_NAME,
    CHANNEL_MAPPINGS,
    CHANNEL_MAP,
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
    if not API_ID or not API_HASH:
        print("Error: Telegram API configuration is incomplete.")
        print("Please set TG_API_ID and TG_API_HASH environment variables.")
        return

    if not CHANNEL_MAPPINGS:
        print("Error: No channel mappings configured.")
        print("Set CHANNEL_MAPPINGS (JSON array) or TG_TARGET_CHANNEL_ID in your .env file.")
        return

    # Log all configured channel mappings at startup
    print(f"Configured {len(CHANNEL_MAPPINGS)} channel mapping(s):")
    for mapping in CHANNEL_MAPPINGS:
        discord_status = "✓ Discord" if mapping.discord_webhook_url else "✗ No Discord"
        print(f"  [{mapping.label}] Telegram {mapping.telegram_channel_id} → {discord_status}")

    if SESSION_STRING:
        from telethon.sessions import StringSession
        client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    else:
        client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

    # Collect all Telegram channel IDs to listen on
    all_channel_ids = [m.telegram_channel_id for m in CHANNEL_MAPPINGS]

    async def process_telegram_message(message, channel_id):
        # Look up the mapping for this channel
        mapping = CHANNEL_MAP.get(channel_id)
        if not mapping:
            print(f"Warning: No mapping found for channel {channel_id}. Skipping.")
            return

        media_path = None
        if message.media:
            # Create a temp directory if it doesn't exist
            if not os.path.exists("temp_media"):
                os.makedirs("temp_media")
            try:
                # Download media
                print(f"[{mapping.label}] Downloading media for message {message.id}...")
                media_path = await message.download_media(file="temp_media/")
                print(f"[{mapping.label}] Downloaded media to: {media_path}")
            except Exception as e:
                print(f"[{mapping.label}] Failed to download media: {e}")
                
        # Extract the text of the message, or note if it is media without a caption
        if message.text:
            text = message.text
        elif message.media:
            text = "[Media received without caption]"
        else:
            text = "[Empty or unknown message format]"
            
        print(f"[{mapping.label}] Processing message {message.id} from channel {channel_id}:\n{text}")
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
        
        # Dispatch the Discord logging task — route to this channel's webhook
        asyncio.create_task(send_to_discord_async(text, media_path, mapping.discord_webhook_url))
        
        # AI Trade Extraction
        trade_data = await extract_trade_async(text)
        if trade_data:
            formatted_time = message_date_ist.strftime("%Y-%m-%d %H:%M:%S")
            asyncio.create_task(append_to_sheet_async(trade_data, formatted_time))
        
        # Update checkpoint
        write_checkpoint(channel_id, message.id)

    @client.on(events.NewMessage(chats=all_channel_ids))
    async def handler(event):
        await process_telegram_message(event.message, event.chat_id)

    @client.on(events.NewMessage())
    async def debug_handler(event):
        # Print the chat ID and message of ANY incoming message
        chat_id = event.chat_id
        text = event.message.message or "[No text/Media]"
        
        if chat_id not in CHANNEL_MAP:
            print(f"[DEBUG] Received a message from chat ID: {chat_id}")
            print(f"[DEBUG] Message Content: {text}")
            print(f"[DEBUG] If this is your channel, add it to CHANNEL_MAPPINGS!")
            print("-" * 40)

    print("Starting Telegram UserBot...")
    await client.start()
    
    print(f"Successfully connected! Checking for missed messages across {len(CHANNEL_MAPPINGS)} channel(s)...")
    
    # Catch-up phase: process each mapped channel independently
    for mapping in CHANNEL_MAPPINGS:
        channel_id = mapping.telegram_channel_id
        last_id = read_checkpoint(channel_id)
        if last_id is not None:
            print(f"[{mapping.label}] Found checkpoint! Fetching missed messages after ID {last_id}...")
            missed_messages = []
            async for message in client.iter_messages(channel_id, min_id=last_id, reverse=True):
                missed_messages.append(message)
            
            if missed_messages:
                print(f"[{mapping.label}] Found {len(missed_messages)} missed messages. Catching up...")
                for msg in missed_messages:
                    await process_telegram_message(msg, channel_id)
                print(f"[{mapping.label}] Catch-up complete!")
            else:
                print(f"[{mapping.label}] No missed messages found.")
        else:
            print(f"[{mapping.label}] No checkpoint found. Starting fresh.")
            latest = await client.get_messages(channel_id, limit=1)
            if latest:
                write_checkpoint(channel_id, latest[0].id)
                print(f"[{mapping.label}] Created initial checkpoint at message ID {latest[0].id}.")

    channel_labels = ", ".join(m.label for m in CHANNEL_MAPPINGS)
    print(f"Listening for new live messages in: [{channel_labels}]")
    
    # Setup graceful shutdown for Railway zero-downtime deployments
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    
    def shutdown_handler(*args):
        print("\nReceived termination signal. Initiating graceful shutdown...")
        stop_event.set()
        
    import signal
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_handler, sig)
        except NotImplementedError:
            # Fallback for Windows if run locally
            signal.signal(sig, shutdown_handler)
            
    # Keep the script running until a signal is received
    try:
        # We use wait() with a timeout in a loop so it can catch exceptions if needed,
        # but wait() directly works too. To support Windows fallbacks better, we use a loop.
        while not stop_event.is_set():
            await asyncio.sleep(1)
    finally:
        print("Disconnecting Telegram client to prevent AuthKeyDuplicatedError on next run...")
        await client.disconnect()
        print("Disconnected cleanly.")

if __name__ == '__main__':
    # Run the main async loop
    asyncio.run(main())
