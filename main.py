"""
Decentralized Signal Broadcaster — FastAPI + Telethon + WebSocket Server.

Replaces the original asyncio.run(main()) entrypoint with a FastAPI application
that wraps the Telethon client in its lifespan, exposes a WebSocket endpoint
for trade signal broadcasting, and fixes latency traps.

Start with:
    uvicorn main:app --host 0.0.0.0 --port $PORT
"""

import os
import sys
import asyncio
import signal
import time
import zoneinfo
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from starlette.websockets import WebSocketState

from telethon import TelegramClient, events

from config import (
    API_ID, 
    API_HASH, 
    SESSION_STRING, 
    SESSION_NAME,
    CHANNEL_MAPPINGS,
    CHANNEL_MAP,
    LOG_GROUP_ID,
)
from services.scrip_service import load_valid_scrips
from state_manager import (
    read_checkpoint, 
    write_checkpoint, 
    read_last_date, 
    write_last_date
)
from services.notion_service import send_to_notion_async
from services.discord_service import send_to_discord_async
from services.gemini_service import extract_trade_async, hybrid_extract_trade
from services.regex_service import filter_message
from services.google_sheets_service import append_to_sheet_async

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("broadcaster")

# --- WebSocket Authentication ---
WS_AUTH_TOKEN = os.environ.get("WS_AUTH_TOKEN", "")

# --- IST Timezone (per AGENTS.md: never remove this) ---
IST_ZONE = zoneinfo.ZoneInfo("Asia/Kolkata")


# ============================================================================
# Telegram Trade Logger
# ============================================================================

_log_channel_disabled = False
_log_entity = None

async def send_trade_log(message: str):
    """
    Send a trade log message to the Telegram log group.
    Non-blocking — fire and forget via asyncio.create_task.
    Auto-disables if the account lacks write permissions.
    """
    global _log_channel_disabled, _log_entity
    if _log_channel_disabled or not LOG_GROUP_ID or not _telegram_client:
        return
    try:
        clean = message.strip()
        if clean:
            # Use the resolved entity if we have it, otherwise fallback to the ID
            target = _log_entity if _log_entity else LOG_GROUP_ID
            await _telegram_client.send_message(target, clean)
    except Exception as e:
        error_msg = str(e)
        if "can't write" in error_msg.lower() or "ChatWriteForbiddenError" in error_msg:
            logger.warning(
                f"Cannot write to log channel {LOG_GROUP_ID}. "
                f"Make sure the Telegram account is an admin with post permissions. "
                f"Trade logging disabled for this session."
            )
            _log_channel_disabled = True
        elif "invalid peer" in error_msg.lower():
            logger.error(f"Failed to resolve log group peer. Disabling logs. Error: {e}")
            _log_channel_disabled = True
        else:
            logger.error(f"Telegram trade log error: {e}")


# ============================================================================
# WebSocket Connection Manager
# ============================================================================

class ConnectionManager:
    """Manages active WebSocket connections for trade signal broadcasting."""

    def __init__(self):
        self._connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self._connections.add(websocket)
        logger.info(f"WebSocket client connected. Active: {len(self._connections)}")

    def disconnect(self, websocket: WebSocket):
        self._connections.discard(websocket)
        logger.info(f"WebSocket client disconnected. Active: {len(self._connections)}")

    async def broadcast(self, data: dict):
        """
        Send trade signal JSON to all connected clients.
        Silently removes broken connections.
        """
        if not self._connections:
            return

        broken = []
        for ws in self._connections:
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_json(data)
            except Exception:
                broken.append(ws)

        for ws in broken:
            self._connections.discard(ws)
            logger.warning(f"Removed broken WebSocket connection. Active: {len(self._connections)}")

    @property
    def active_count(self) -> int:
        return len(self._connections)


manager = ConnectionManager()


# ============================================================================
# Telegram Client Setup
# ============================================================================

def create_telegram_client() -> TelegramClient:
    """Create and return the Telethon client based on config."""
    if SESSION_STRING:
        from telethon.sessions import StringSession
        return TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    else:
        return TelegramClient(SESSION_NAME, API_ID, API_HASH)


# Global reference so the lifespan and handlers can share it
_telegram_client: TelegramClient | None = None


# ============================================================================
# Core Message Processing
# ============================================================================

async def process_telegram_message(message, channel_id):
    """
    Process a single Telegram message:
    1. Dispatch Notion logging (background)
    2. Dispatch Discord forwarding with raw message object (background, media downloaded off critical path)
    3. Run hybrid F&O extraction (regex → Gemini)
    4. If F&O signal found → broadcast to WebSocket clients
    5. Run equity trade extraction → Google Sheets (background)
    6. Update checkpoint
    """
    # Look up the mapping for this channel
    mapping = CHANNEL_MAP.get(channel_id)
    if not mapping:
        logger.warning(f"No mapping found for channel {channel_id}. Skipping.")
        return

    # Extract the text of the message, or note if it is media without a caption
    if message.text:
        text = message.text
    elif message.media:
        text = "[Media received without caption]"
    else:
        text = "[Empty or unknown message format]"
        
    logger.info(f"[{mapping.label}] Processing message {message.id} from channel {channel_id}:\n{text}")
    logger.info("-" * 40)
    
    # Check if it's a new day in IST
    message_date_ist = message.date.astimezone(IST_ZONE)
    msg_date_str = message_date_ist.strftime("%Y-%m-%d")
    
    last_date_str = read_last_date(channel_id)
    is_new_day = False
    if last_date_str != msg_date_str:
        is_new_day = True
        write_last_date(channel_id, msg_date_str)
    
    # --- Background Tasks (non-blocking) ---
    
    # Dispatch Notion logging
    asyncio.create_task(send_to_notion_async(text, message.date, is_new_day))
    
    # Dispatch Discord forwarding — pass raw message object, media downloads off critical path
    asyncio.create_task(
        send_to_discord_async(text, message=message, webhook_url=mapping.discord_webhook_url)
    )
    
    # --- Equity Signal Extraction & WebSocket Broadcast (opt-in per channel) ---
    formatted_time = message_date_ist.strftime("%Y-%m-%d %H:%M:%S")
    
    if mapping.enable_trading:
        # Apply message-level filters (index blocker, sell blocker, etc.)
        block_reason = filter_message(text)
        if block_reason:
            logger.info(f"[{mapping.label}] 🛡️ BLOCKED: {block_reason}")
            asyncio.create_task(send_trade_log(f"[🛡️] BLOCKED: {block_reason}\n\nMessage: {text[:100]}"))
        else:
            # Log signal received
            asyncio.create_task(send_trade_log(f"[🚀] Signal Received: {text}"))
            
            t_start = time.perf_counter_ns()
            try:
                trade_signal = await hybrid_extract_trade(text, timestamp=formatted_time)
                t_extract = time.perf_counter_ns()
                extract_ms = (t_extract - t_start) / 1_000_000
                
                if trade_signal:
                    symbol = trade_signal['stock_symbol']
                    price = trade_signal['entry_price']
                    order_type = trade_signal['order_type']
                    source = trade_signal['source']
                    price_display = 'MARKET' if price == 0 else price
                    
                    # Log extraction result
                    if source == 'REGEX':
                        asyncio.create_task(send_trade_log(
                            f"[*] Local Fast-Path Parsed -> Symbol: {symbol} | Price: {price_display}"
                        ))
                    else:
                        asyncio.create_task(send_trade_log(
                            f"[*] AI Extracted -> Symbol: {symbol} | Price: {price_display}"
                        ))
                    
                    # Inject latency metadata into the broadcast payload
                    trade_signal["_latency_ms"] = {
                        "extraction": round(extract_ms, 2),
                        "source": source,
                    }
                    
                    await manager.broadcast(trade_signal)
                    t_broadcast = time.perf_counter_ns()
                    broadcast_ms = (t_broadcast - t_extract) / 1_000_000
                    total_ms = (t_broadcast - t_start) / 1_000_000
                    
                    trade_signal["_latency_ms"]["broadcast"] = round(broadcast_ms, 2)
                    trade_signal["_latency_ms"]["total_server"] = round(total_ms, 2)
                    
                    # Compute limit price with 1% buffer (for log display)
                    if order_type == 'LIMIT' and price > 0:
                        buffer_price = float(f"{round((price * 1.01) / 0.05) * 0.05:.2f}")
                    else:
                        buffer_price = 0.0
                    
                    asyncio.create_task(send_trade_log(
                        f"[*] Executing -> Symbol: {symbol} | Segment: NSE_EQ "
                        f"| QTY: 1 | Type: {order_type} | Price: {buffer_price}\n"
                        f"[📡] Broadcast to {manager.active_count} client(s) in {total_ms:.1f}ms"
                    ))
                    
                    logger.info(
                        f"[⚡ LATENCY] {symbol} "
                        f"@ {price_display} "
                        f"| extract={extract_ms:.1f}ms ({source}) "
                        f"| broadcast={broadcast_ms:.1f}ms "
                        f"| total={total_ms:.1f}ms"
                    )
                else:
                    logger.debug(
                        f"[{mapping.label}] No equity signal found ({extract_ms:.1f}ms)"
                    )
            except Exception as e:
                logger.error(f"Equity hybrid extraction failed: {e}", exc_info=True)
                asyncio.create_task(send_trade_log(f"[X] Extraction error: {e}"))
    else:
        logger.debug(f"[{mapping.label}] Trading disabled for this channel. Skipping extraction.")
    
    # --- Equity Trade Extraction → Google Sheets (existing pipeline, preserved) ---
    try:
        trade_data = await extract_trade_async(text)
        if trade_data:
            asyncio.create_task(append_to_sheet_async(trade_data, formatted_time))
    except Exception as e:
        logger.error(f"Equity trade extraction failed: {e}", exc_info=True)
    
    # Update checkpoint
    write_checkpoint(channel_id, message.id)


# ============================================================================
# Telegram Event Handlers & Catch-Up
# ============================================================================

async def start_telegram_listener():
    """Start the Telethon client, register handlers, and run catch-up."""
    global _telegram_client

    if not API_ID or not API_HASH:
        logger.error("Telegram API configuration is incomplete. Set TG_API_ID and TG_API_HASH.")
        return

    if not CHANNEL_MAPPINGS:
        logger.error("No channel mappings configured. Set CHANNEL_MAPPINGS or TG_TARGET_CHANNEL_ID.")
        return

    # Log configured channels
    logger.info(f"Configured {len(CHANNEL_MAPPINGS)} channel mapping(s):")
    for mapping in CHANNEL_MAPPINGS:
        discord_status = "✓ Discord" if mapping.discord_webhook_url else "✗ No Discord"
        trading_status = "✓ Trading" if mapping.enable_trading else "✗ No Trading"
        logger.info(f"  [{mapping.label}] Telegram {mapping.telegram_channel_id} → {discord_status} | {trading_status}")

    client = create_telegram_client()
    _telegram_client = client

    all_channel_ids = [m.telegram_channel_id for m in CHANNEL_MAPPINGS]

    @client.on(events.NewMessage(chats=all_channel_ids))
    async def handler(event):
        try:
            await process_telegram_message(event.message, event.chat_id)
        except Exception as e:
            logger.error(f"Error in message handler: {e}", exc_info=True)

    @client.on(events.NewMessage())
    async def debug_handler(event):
        # Print the chat ID and message of ANY incoming message
        chat_id = event.chat_id
        text = event.message.message or "[No text/Media]"
        
        if chat_id not in CHANNEL_MAP:
            logger.debug(f"[DEBUG] Received a message from chat ID: {chat_id}")
            logger.debug(f"[DEBUG] Message Content: {text}")
            logger.debug(f"[DEBUG] If this is your channel, add it to CHANNEL_MAPPINGS!")

    logger.info("Starting Telegram UserBot...")
    await client.start()
    
    logger.info(f"Connected! Catching up across {len(CHANNEL_MAPPINGS)} channel(s)...")

    # --- Concurrent Catch-Up (latency fix: was sequential, now uses asyncio.gather) ---
    for mapping in CHANNEL_MAPPINGS:
        channel_id = mapping.telegram_channel_id
        last_id = read_checkpoint(channel_id)
        if last_id is not None:
            logger.info(f"[{mapping.label}] Found checkpoint! Fetching missed messages after ID {last_id}...")
            missed_messages = []
            async for message in client.iter_messages(channel_id, min_id=last_id, reverse=True):
                missed_messages.append(message)
            
            if missed_messages:
                logger.info(f"[{mapping.label}] Found {len(missed_messages)} missed messages. Catching up concurrently...")
                await asyncio.gather(
                    *[process_telegram_message(msg, channel_id) for msg in missed_messages]
                )
                logger.info(f"[{mapping.label}] Catch-up complete!")
            else:
                logger.info(f"[{mapping.label}] No missed messages found.")
        else:
            logger.info(f"[{mapping.label}] No checkpoint found. Starting fresh.")
            latest = await client.get_messages(channel_id, limit=1)
            if latest:
                write_checkpoint(channel_id, latest[0].id)
                logger.info(f"[{mapping.label}] Created initial checkpoint at message ID {latest[0].id}.")

    channel_labels = ", ".join(m.label for m in CHANNEL_MAPPINGS)
    logger.info(f"Listening for new live messages in: [{channel_labels}]")

    # Send startup notification to Telegram log channel
    if LOG_GROUP_ID:
        try:
            global _log_entity
            # Remove the -100 prefix if it exists to get the bare entity ID
            bare_id = abs(LOG_GROUP_ID)
            if str(LOG_GROUP_ID).startswith("-100"):
                bare_id = int(str(LOG_GROUP_ID)[4:])

            async for dialog in client.iter_dialogs(limit=None):
                if dialog.id == LOG_GROUP_ID or getattr(dialog.entity, 'id', 0) == bare_id:
                    _log_entity = dialog.entity
                    break
            
            if not _log_entity:
                _log_entity = await client.get_entity(LOG_GROUP_ID)
                
            trading_channels = [m.label for m in CHANNEL_MAPPINGS if m.enable_trading]
            await send_trade_log(
                f"🟢 **Trading Bot Engine Started (Equity Mode)**\n"
                f"Trading enabled for: {', '.join(trading_channels) or 'none'}\n"
                f"Listening for new signals..."
            )
        except Exception as e:
            logger.error(f"Failed to resolve LOG_GROUP_ID {LOG_GROUP_ID}: {e}. Trade logging disabled.")
            global _log_channel_disabled
            _log_channel_disabled = True


async def stop_telegram_listener():
    """Gracefully disconnect the Telegram client."""
    global _telegram_client
    if _telegram_client:
        logger.info("Disconnecting Telegram client...")
        await _telegram_client.disconnect()
        logger.info("Telegram client disconnected cleanly.")
        _telegram_client = None


# ============================================================================
# FastAPI Application
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan: start the Telegram client on startup,
    tear it down on shutdown.
    """
    # Startup
    logger.info("FastAPI starting up — launching Telegram listener...")
    # Load scrip master in a background thread to not block the event loop
    await asyncio.to_thread(load_valid_scrips)
    telegram_task = asyncio.create_task(start_telegram_listener())

    # Give the client a moment to connect before accepting HTTP/WS traffic
    await asyncio.sleep(2)

    yield

    # Shutdown
    logger.info("FastAPI shutting down — stopping Telegram listener...")
    telegram_task.cancel()
    await stop_telegram_listener()


app = FastAPI(
    title="Darvas Signal Broadcaster",
    description="Low-latency F&O trade signal broadcaster via WebSocket",
    version="2.0.0",
    lifespan=lifespan,
)


# --- Health Check ---

@app.get("/health")
async def health_check():
    """Health check endpoint for Railway / uptime monitors."""
    return {
        "status": "healthy",
        "telegram_connected": _telegram_client is not None and _telegram_client.is_connected() if _telegram_client else False,
        "websocket_clients": manager.active_count,
        "channels_monitored": len(CHANNEL_MAPPINGS),
    }


# --- WebSocket Endpoint ---

@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(default=""),
):
    """
    WebSocket endpoint for trade signal streaming.
    
    Clients connect here to receive real-time F&O trade signals.
    If WS_AUTH_TOKEN is set on the server, clients must pass ?token=<secret>.
    """
    # Authenticate if server has a token configured
    if WS_AUTH_TOKEN and token != WS_AUTH_TOKEN:
        await websocket.close(code=4001, reason="Invalid authentication token")
        logger.warning(f"WebSocket auth rejected from {websocket.client}")
        return

    await manager.connect(websocket)
    try:
        # Keep the connection alive — we only send, clients don't need to send data
        # But we listen for any incoming messages (e.g., pings, keepalives)
        while True:
            try:
                data = await websocket.receive_text()
                # Client can send "ping" as a keepalive
                if data.strip().lower() == "ping":
                    await websocket.send_text("pong")
            except WebSocketDisconnect:
                break
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        manager.disconnect(websocket)


# ============================================================================
# Standalone Fallback (backward compatibility)
# ============================================================================

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    logger.info(f"Starting Darvas Signal Broadcaster on port {port}...")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
