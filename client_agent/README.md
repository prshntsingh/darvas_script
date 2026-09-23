# Trading Signal Execution Client

A standalone WebSocket client that connects to the Darvas Signal Broadcaster, receives equity trade signals in real-time, and executes them on your brokerage account via Dhan or Zerodha APIs.

## Architecture

```
┌──────────────────────────────┐       WebSocket        ┌──────────────────────────┐
│  Signal Broadcaster (Server) │  ──────────────────►   │  Execution Client (You)  │
│  Hosted on Railway/Cloud     │   JSON trade signals   │  Your PC / GCP VM        │
│                              │                        │                          │
│  • Telegram listener         │                        │  • Dhan / Zerodha broker  │
│  • Signal extraction         │                        │  • Auto-reconnect         │
│  • WebSocket broadcast       │                        │  • DRY_RUN mode           │
└──────────────────────────────┘                        └──────────────────────────┘
```

## Why Run Separately?

SEBI and Zerodha require trading API calls to originate from a **static, whitelisted IP**. The broadcaster server runs on Railway (dynamic IPs), so we decouple:

- **Server (Railway):** Processes Telegram → extracts signals → broadcasts via WebSocket
- **Client (Your PC / GCP VM):** Receives signals → executes trades from a static IP

---

## Setup Guide

### Step 1: Get a Static IP

**Option A: Your Home PC (easiest)**
- Your home IP is relatively static. Whitelist it with your broker.
- If your ISP changes your IP, update the whitelist.

**Option B: GCP Compute Engine (recommended for 24/7)**
1. Create a small VM (e2-micro is free tier eligible)
2. Reserve a static external IP in GCP Console → VPC Network → IP Addresses
3. Attach the static IP to your VM
4. Whitelist this IP with your broker

### Step 2: Whitelist Your IP

**For Dhan:**
1. Log in to [Dhan Developer Portal](https://api.dhan.co)
2. Go to API Settings → IP Whitelist
3. Add your static IP (from Step 1)

**For Zerodha (Kite Connect):**
1. Log in to [Kite Connect Developer Portal](https://developers.kite.trade)
2. Go to your App → Settings
3. Add your static IP under "Allowed IPs"

### Step 3: Install Dependencies

```bash
cd client_agent
pip install -r requirements.txt
```

### Step 4: Configure Environment Variables

Copy the example and fill in your credentials:

```bash
cp .env.example .env
```

Edit `.env`:

```bash
# WebSocket server URL (your broadcaster deployment)
WS_SERVER_URL=ws://your-app.up.railway.app/ws

# Auth token (must match the server's WS_AUTH_TOKEN, leave empty if none)
WS_AUTH_TOKEN=

# Broker: "dhan" or "zerodha"
BROKER=dhan

# --- Dhan Credentials (if BROKER=dhan) ---
DHAN_CLIENT_ID=your_dhan_client_id
# Leave DHAN_ACCESS_TOKEN blank if you want the client to auto-generate it at startup!
DHAN_ACCESS_TOKEN=
# (Optional) Dhan Auto-Login credentials for 24/7 self-healing authentication:
DHAN_PIN=your_6_digit_pin
DHAN_TOTP_SECRET=your_totp_secret_key

# --- Zerodha Credentials (if BROKER=zerodha) ---
# KITE_API_KEY=your_kite_api_key
# KITE_ACCESS_TOKEN=your_kite_access_token

# --- Execution Filters ---
# Comma-separated list of channel labels to execute trades for (empty = ALL channels).
ALLOWED_CHANNELS=premium,test

# --- Capital Allocation ---
# Amount to invest per trade in INR (e.g., 10000). 
TRADE_AMOUNT_INR=10000
# Fixed quantity fallback for MARKET orders if live price fetch fails.
DEFAULT_QUANTITY=10

# IMPORTANT: Start with DRY_RUN=true to test without placing real orders
DRY_RUN=true
```

### Step 5: Test in DRY RUN Mode

```bash
python client.py
```

You should see:
```
Starting Trading Signal Execution Client...
Broker: DHAN
Downloading Dhan scrip master CSV...
Loaded 4500+ equity scrips from Dhan!
Using Dhan HQ broker.
Running in DRY RUN mode. Orders will be simulated.
Connecting to ws://your-app.up.railway.app/ws...
Connected to broadcaster server.
```

When a signal arrives:
```
Received signal: SBIN @ 850.0 (LIMIT) via REGEX
[⚡ SERVER LATENCY] extraction=0.3ms (REGEX) | broadcast=1.2ms | total_server=1.5ms
Processing equity signal for SBIN
[☀️ LIVE] Placing live order for SBIN
Executing → Symbol: SBIN | Segment: NSE_EQ | QTY: 1 | Type: LIMIT | Price: 858.50
[DRY RUN] Would send Dhan order: { ... }
[⚡ CLIENT LATENCY] broker_exec=0.1ms | client_total=0.5ms | server+client=2.0ms
```

### Step 6: Go Live

Once you've verified the signals look correct:

```bash
# Edit .env
DRY_RUN=false
```

Then restart:
```bash
python client.py
```

⚠️ **WARNING**: With `DRY_RUN=false`, real orders will be placed on your broker account.

---

## Signal Format

The client receives JSON signals via WebSocket:

```json
{
  "stock_symbol": "SBIN",
  "entry_price": 850.0,
  "order_type": "LIMIT",
  "source": "REGEX",
  "raw_text": "Buy #SBIN @ 850",
  "timestamp": "2026-09-20 12:36:23",
  "channel_label": "premium",
  "asset_class": "EQUITY",
  "signal_id": "-1001234567890:4521",
  "_latency_ms": {
    "extraction": 0.3,
    "source": "REGEX",
    "broadcast": 1.2,
    "total_server": 1.5
  }
}
```

| Field | Description |
|-------|-------------|
| `stock_symbol` | NSE ticker symbol (e.g., `SBIN`, `RELIANCE`, `TATAMOTORS`) |
| `entry_price` | Entry price. `0` = MARKET order |
| `order_type` | `MARKET` or `LIMIT` |
| `source` | `REGEX` (fast-path, <1ms) or `GEMINI` (AI fallback) |
| `asset_class` | `EQUITY` (handled here) or `FNO` (ignored here; handled by `fno_agent`) |
| `signal_id` | `<telegram_channel_id>:<message_id>`, unique per Telegram message |

## Order Logic

- **BUY-ONLY** mode — no sell/short/exit orders
- **CNC** product type (delivery, not intraday)
- **LIMIT** orders get a 1% price buffer, rounded to tick size (₹0.05)
- **MARKET** orders fetch the exact live real-time price instantly using Yahoo Finance (Dhan) or Kite Connect APIs.
- **AMO** (After Market Orders) auto-detected: weekends + before 9:15 AM / after 3:15 PM IST
- **Quantity**: Automatically calculated based on `TRADE_AMOUNT_INR / Price`.
- **Auto-Login**: Dhan token auto-refreshes seamlessly in the middle of a trade if a `401 Unauthorized` token expiry occurs!

## Auto-Reconnection

The client automatically reconnects if the WebSocket connection drops:
- Exponential backoff: 1s → 2s → 4s → 8s → ... → max 60s
- Resets to 1s on successful reconnection
- Graceful shutdown via Ctrl+C

## Running 24/7 on GCP

See **[DEPLOY.md](DEPLOY.md)**. It covers the equity broadcaster on Railway, and the one-command setup of this bot on the VM with the `bot` command for everyday use. The options bot has its own guide in [`fno_agent/DEPLOY.md`](../fno_agent/DEPLOY.md).

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `Scrip not found for 'XYZ'` | Symbol not in Dhan equity master. Check exact NSE ticker. |
| `Invalid_Authentication` / `DH-901` | Dhan access token expired. Generate a new one at api.dhan.co |
| `WebSocket connection error` | Server might be down or URL is wrong. Check `WS_SERVER_URL`. |
| `Running in DRY RUN mode` | Set `DRY_RUN=false` in `.env` for live trading. |
| No signals received | Check server health: `curl https://<server>/health` |
| Service keeps restarting | `bot logs equity` shows the crash. Usually a setting is wrong (`bot setup equity`) or packages are missing (`bot update`). |
| `Initial Dhan auto-login failed. Exiting.` | Check `DHAN_PIN` / `DHAN_TOTP_SECRET` and that the VM clock is correct (`timedatectl`), since TOTP depends on the time. systemd retries every 5s. |
| Orders rejected for IP | The VM's external IP must be the reserved static IP that is whitelisted with Dhan: `curl -s ifconfig.me`. |
| Bot not running after reboot | Run `bot setup equity` again. |
