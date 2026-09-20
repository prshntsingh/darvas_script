import requests
import asyncio
import os


def send_to_discord_sync(text, message=None, media_path=None, webhook_url=None):
    """
    Synchronous function to post a message (and optionally media) to a Discord Webhook.
    
    Accepts either a pre-downloaded media_path OR a raw Telethon message object.
    If a message object is provided (and has media), it downloads the media here
    — off the critical path of the Telegram event handler.
    """
    if not webhook_url:
        # Silently skip if no webhook URL is provided
        return

    # If a Telethon message object was passed with media, download it now
    # (this runs in a thread executor, so we need to handle the async download)
    downloaded_path = media_path
    if downloaded_path is None and message is not None and hasattr(message, 'media') and message.media:
        try:
            if not os.path.exists("temp_media"):
                os.makedirs("temp_media")
            # Use synchronous download via Telethon's download_media
            # Since we're in a thread executor, we create a new event loop for this
            loop = asyncio.new_event_loop()
            try:
                downloaded_path = loop.run_until_complete(message.download_media(file="temp_media/"))
                print(f"[Discord] Downloaded media to: {downloaded_path}")
            finally:
                loop.close()
        except Exception as e:
            print(f"[Discord] Failed to download media for Discord: {e}")

    data = {"content": text}
    try:
        if downloaded_path and os.path.exists(downloaded_path):
            with open(downloaded_path, "rb") as f:
                response = requests.post(webhook_url, data=data, files={"file": f})
            # Cleanup the local file after upload
            try:
                os.remove(downloaded_path)
                print(f"Cleaned up temporary file: {downloaded_path}")
            except Exception as cleanup_err:
                print(f"Failed to delete temporary file {downloaded_path}: {cleanup_err}")
        else:
            response = requests.post(webhook_url, json=data)
        
        response.raise_for_status()
        print("Successfully logged message to Discord.")
    except Exception as e:
        print(f"Failed to log to Discord: {e}")
        if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
            print(f"Discord API Error: {e.response.text}")


async def send_to_discord_async(text, message=None, media_path=None, webhook_url=None):
    """Asynchronously run the blocking Discord API function."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, send_to_discord_sync, text, message, media_path, webhook_url)
