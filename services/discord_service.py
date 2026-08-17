import requests
import asyncio
import os

def send_to_discord_sync(text, media_path=None, webhook_url=None):
    """Synchronous function to post a message (and optionally media) to a Discord Webhook."""
    if not webhook_url:
        # Silently skip if no webhook URL is provided
        return

    data = {"content": text}
    try:
        if media_path and os.path.exists(media_path):
            with open(media_path, "rb") as f:
                response = requests.post(webhook_url, data=data, files={"file": f})
            # Cleanup the local file after upload
            try:
                os.remove(media_path)
                print(f"Cleaned up temporary file: {media_path}")
            except Exception as cleanup_err:
                print(f"Failed to delete temporary file {media_path}: {cleanup_err}")
        else:
            response = requests.post(webhook_url, json=data)
        
        response.raise_for_status()
        print("Successfully logged message to Discord.")
    except Exception as e:
        print(f"Failed to log to Discord: {e}")
        if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
            print(f"Discord API Error: {e.response.text}")

async def send_to_discord_async(text, media_path=None, webhook_url=None):
    """Asynchronously run the blocking Discord API function."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, send_to_discord_sync, text, media_path, webhook_url)
