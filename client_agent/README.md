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

## Running 24/7 on GCP (simple guide)

You only type a few commands. A setup script asks you questions and handles the rest. Once it's set up, the bots:
- start by themselves when the VM starts
- restart by themselves if they crash
- log in to Dhan by themselves every morning

### Before you start, keep these ready
- **Server address** and **server password**. Ask whoever runs the Railway server. The address looks like `wss://something.up.railway.app/ws`.
- **Dhan Client ID**, **Dhan login PIN** and **Dhan TOTP secret**. The TOTP secret is the long code Dhan shows when you turn on TOTP under *DhanHQ Trading APIs*.
- How many **rupees per trade** you want each bot to use.
- The VM's **static IP must be whitelisted in Dhan**. This is a one-time step; see "Step 1" and "Step 2" at the top of this page.

### One-time setup (about 5 minutes)

1. Open the Google Cloud Console → **Compute Engine → VM instances**. Click **SSH** next to your VM. A black terminal window opens.
2. Copy and paste this line, then press Enter:
   ```bash
   git clone https://github.com/prshntsingh/darvas_script.git ~/darvas_script && cd ~/darvas_script && bash deploy/setup_vm.sh
   ```
   If the folder `~/darvas_script` already exists, use this instead:
   ```bash
   cd ~/darvas_script && git pull && bash deploy/setup_vm.sh
   ```
3. Answer the questions. Type your answer and press Enter. Secret answers (PIN, passwords) stay invisible while you type; that's normal. To keep the value shown in `[brackets]`, just press Enter.
4. At the end you should see green ✔ lines saying each bot is **running and connected**, in **TEST mode**.

The bots start in **TEST mode**: they receive signals and show what they would buy, but place **no real orders**. Leave them like that for a day or two and check with `bot today`.

### Everyday commands

Open the VM's SSH window and type any of these:

| Type this | What it does |
|---|---|
| `bot status` | Shows whether each bot is running, and whether it's in TEST or LIVE mode |
| `bot today` | Today's signals, trades and errors |
| `bot logs` | Watch the bots live. Press **Ctrl+C** to stop watching; the bots keep running |
| `bot live equity` | Start placing **real** share orders (asks you to type `YES`) |
| `bot test equity` | Go back to test mode (no real orders) |
| `bot live fno` / `bot test fno` | The same for the options bot |
| `bot restart` | Restart the bots. This fixes most problems |
| `bot settings equity` | Change a setting, e.g. the amount per trade. Save with **Ctrl+O**, Enter, then exit with **Ctrl+X**. The bot restarts by itself |
| `bot setup` | Run the question-and-answer setup again (your old answers are kept) |
| `bot update` | Download the newest version of the bots and restart them |
| `bot help` | List all commands |

Add `equity` or `fno` to `logs`, `today`, `restart`, `stop` or `start` to act on just one bot, e.g. `bot logs fno`.

Try not to run `bot update` or `bot restart` during market hours (09:15–15:30). Signals that arrive while a bot is restarting are missed.

### If something looks wrong

1. Run `bot status`. If a bot says **NOT RUNNING**, run `bot restart`.
2. Still not working? Run `bot logs`, wait 30 seconds, and send a screenshot to whoever maintains the bots.
3. After changing your Dhan PIN or TOTP, or if the server address changes, run `bot setup` again.

### What the setup does (for the technical person)

- Installs `git`, `python3-venv` and `nano`. Sets the VM timezone to IST. Caps journald logs at 500 MB.
- Creates `.venv` and installs `client_agent/requirements.txt` and `fno_agent/requirements.txt`.
- Writes `client_agent/.env` and `fno_agent/.env` with `chmod 600`. It sets `BROKER=dhan` and leaves `DHAN_ACCESS_TOKEN` blank, so each start does a fresh TOTP login. Other keys, such as `ALLOWED_CHANNELS`, are kept.
- Installs systemd units from `deploy/`, with the user and paths filled in:
  - `trading-client.service`: runs the equity client with `Restart=always`.
  - `trading-client-restart.timer`: restarts the equity client Mon–Fri at 08:45 IST, whatever the VM's timezone. The client only loads the scrip master at startup, and on its own only re-logs in to Dhan after an order is rejected. The restart gives it both fresh every morning.
  - `fno-agent.service`: the FnO agent. It refreshes its own session and instruments daily.
- Links `/usr/local/bin/bot` → `./bot` (the everyday command). Shared helpers are in `deploy/lib.sh`.
- Restarts each selected service and waits for `Connected to broadcaster` in its journal.

Only `BROKER=dhan` is set up this way. The Zerodha path can't trade equity signals unattended: its token expires daily, and `ZerodhaBroker.handle_signal` expects F&O-style `action` fields.

The equity client ignores signals with `"asset_class": "FNO"` and the FnO agent ignores the rest, so each signal is traded once. Both log in to the same Dhan account. If `bot today equity` shows `Triggering mid-trade auto-login` every day, the two logins are cancelling each other out; tell the maintainer.

Raw systemd commands still work, e.g. `systemctl status trading-client fno-agent` and `journalctl -u trading-client -f`.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `Scrip not found for 'XYZ'` | Symbol not in Dhan equity master. Check exact NSE ticker. |
| `Invalid_Authentication` / `DH-901` | Dhan access token expired. Generate a new one at api.dhan.co |
| `WebSocket connection error` | Server might be down or URL is wrong. Check `WS_SERVER_URL`. |
| `Running in DRY RUN mode` | Set `DRY_RUN=false` in `.env` for live trading. |
| No signals received | Check server health: `curl https://<server>/health` |
| Service keeps restarting | `bot logs equity` shows the crash. Usually a setting is wrong (`bot setup`) or packages are missing (`bot update`). |
| `Initial Dhan auto-login failed. Exiting.` | Check `DHAN_PIN` / `DHAN_TOTP_SECRET` and that the VM clock is correct (`timedatectl`), since TOTP depends on the time. systemd retries every 5s. |
| Orders rejected for IP | The VM's external IP must be the reserved static IP that is whitelisted with Dhan: `curl -s ifconfig.me`. |
| Bot not running after reboot | Run `bot setup` again and answer **Y** for that bot. |
