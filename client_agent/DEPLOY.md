# Deploying the Equity Bot

The equity bot has two parts:

```
Telegram ──► EQUITY broadcaster (Railway) ──wss──► Equity bot (Google Cloud VM, static IP) ──► Dhan
```

- **Part 1:** the broadcaster on Railway. The technical person sets this up once.
- **Part 2:** the bot on the VM. One command, then answer questions.

The options (FnO) bot has its **own** broadcaster and its own guide: [`fno_agent/DEPLOY.md`](../fno_agent/DEPLOY.md). Both bots can run on the same VM.

---

## Part 1 — Equity broadcaster on Railway (technical)

The equity broadcaster is the Railway service that runs this repo's `main.py` for share signals.

**Variables** (Railway → service → *Variables*):

| Variable | Value |
|---|---|
| `CHANNEL_MAPPINGS` | The share channels, each with `"enable_trading": true`. Leave `enable_fno_trading` out on this server. |
| `WS_AUTH_TOKEN` | A long random password, different from the FnO server's. The bot needs it (Part 2). |
| `TG_API_ID`, `TG_API_HASH`, `TG_SESSION_STRING` | Telegram login. The session string must be **unique to this server** (see below). |
| `VERTEX_PROJECT_ID`, `VERTEX_CREDENTIALS_JSON` | Gemini fallback for messages the regex can't read. |
| `GOOGLE_SHEET_ID`, `NOTION_*`, `LOG_GROUP_ID` | Optional logging. |

`CHANNEL_MAPPINGS` example:
```json
[{"telegram_channel_id": -1001234567890, "label": "premium", "enable_trading": true, "discord_webhook_url": "https://discord.com/api/webhooks/..."}]
```

**Give each broadcaster its own Telegram session.** If the equity and FnO servers share one `TG_SESSION_STRING`, Telegram sees one login running in two places and can log out both. Create a second session for the other server:
```bash
python generate_string_session.py   # log in, copy the printed string into that server's TG_SESSION_STRING
```

**Check the broadcaster:**
- **Health:** open `https://<equity-service>.up.railway.app/health` in a browser. It should say healthy.
- **Address for the bot:** `wss://<equity-service>.up.railway.app/ws`. Give this and the `WS_AUTH_TOKEN` to whoever runs Part 2.

---

## Part 2 — Equity bot on the VM (anyone can do this)

### Keep these ready
- **Equity server address** (`wss://…/ws`) and **equity server password**, from Part 1
- **Dhan Client ID**, **Dhan login PIN**, **Dhan TOTP secret** (the long code Dhan shows when you turn on TOTP under *DhanHQ Trading APIs*)
- **Rupees per share trade**
- The VM's **static IP whitelisted in Dhan**: *DhanHQ Trading APIs → IP whitelist*. This is a one-time step.

### Setup (about 5 minutes)
1. Open the Google Cloud Console → **Compute Engine → VM instances**. Click **SSH** next to the VM.
2. Paste this line and press Enter:
   ```bash
   git clone https://github.com/prshntsingh/darvas_script.git ~/darvas_script && bash ~/darvas_script/client_agent/deploy/setup.sh
   ```
   If `~/darvas_script` already exists (for example, the options bot is already set up), use:
   ```bash
   cd ~/darvas_script && git pull && bash client_agent/deploy/setup.sh
   ```
3. Answer the questions. Use the **equity** server's address and password here, not the options one. If the options bot is already set up, the Dhan details are filled in for you: just press Enter.
4. At the end you should see a green line: **Equity bot is running and connected — TEST mode**.

If an older copy of the equity bot is already running on this VM (in a `tmux` window, or started by hand), stop it first. Otherwise every order is placed twice.

### Test first, then go live
The bot starts in **TEST mode**: it receives signals and shows what it *would* buy, without placing orders.
1. For a day or two, check `bot today equity` after signals arrive.
2. When it looks right, run `bot live equity` and type `YES`.
3. To go back to test mode at any time: `bot test equity`.

### Everyday commands
| Type this | What it does |
|---|---|
| `bot status` | Is it running? TEST or LIVE? |
| `bot today equity` | Today's signals, orders and errors |
| `bot logs equity` | Watch live (press **Ctrl+C** to stop watching; the bot keeps running) |
| `bot restart equity` | Restart. Fixes most problems |
| `bot settings equity` | Change a setting, e.g. `TRADE_AMOUNT_INR`. Save with **Ctrl+O**, Enter, then **Ctrl+X** |
| `bot setup equity` | Answer the setup questions again (e.g. a new server address or Dhan PIN) |
| `bot update` | Install the newest version (outside market hours) |

### What runs automatically
- **Start and restart:** the bot starts when the VM starts, and restarts within 5 seconds if it crashes.
- **Daily fresh start:** every weekday at **08:45 IST** it restarts with a fresh Dhan login and today's stock list.
- **Missed signals:** signals sent while the bot is restarting are **not** replayed, so avoid `bot update`/`bot restart` between 09:15 and 15:30.

### If something looks wrong
| You see | Do this |
|---|---|
| `NOT RUNNING` in `bot status` | `bot restart equity`, then `bot logs equity` |
| Setup says "did not connect" | Wrong server address or password: `bot setup equity`. Also check the broadcaster's `/health` page |
| `Initial Dhan auto-login failed` | Wrong Dhan PIN or TOTP secret: `bot setup equity` |
| Orders rejected for IP | Run `curl -s ifconfig.me` on the VM. That IP must be whitelisted in Dhan |
| `Scrip not found for 'XYZ'` | The signal's symbol isn't a valid NSE/BSE ticker. Nothing was bought |

---

## Technical details
- `client_agent/deploy/setup.sh` does the following:
  - installs git, python3-venv and nano
  - sets the timezone to IST and caps journald logs at 500 MB
  - creates the repo `.venv` and installs `client_agent/requirements.txt`
  - writes `client_agent/.env` (chmod 600) with `BROKER=dhan` and a blank `DHAN_ACCESS_TOKEN`, so every start does a TOTP login; other keys (e.g. `ALLOWED_CHANNELS`) are kept
  - links `/usr/local/bin/bot`
- It installs these systemd units, with the user and repo path filled in:
  - `client_agent/deploy/trading-client.service`: `Restart=always`
  - `client_agent/deploy/trading-client-restart.timer`: `OnCalendar=Mon..Fri 08:45 Asia/Kolkata`
- The client only loads the scrip master at startup, and on its own it only re-logs in to Dhan after an order is rejected. The daily restart keeps both fresh.
- **Shared Dhan token.** Dhan keeps only one valid token per account. The equity and options bots share one token through `~/.dhan_token` (`client_agent/dhan_token.py`), so they don't cancel each other's login. If an order is rejected with `DH-906 Invalid Token`, the bot takes the newer shared token, or logs in again, and retries the order once.
- Only `BROKER=dhan` is supported for unattended running. Zerodha's token expires daily, and `ZerodhaBroker.handle_signal` expects F&O-style `action` fields.
- The raw systemd commands still work: `systemctl status trading-client`, `journalctl -u trading-client -f`.
