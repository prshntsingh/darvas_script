# Deploying the Options (FnO) Bot

The options bot has two parts:

```
Telegram ──► FnO broadcaster (Railway) ──wss──► Options bot (Google Cloud VM, static IP) ──► Dhan
```

- **Part 1:** the FnO broadcaster on Railway. The technical person sets this up once.
- **Part 2:** the bot on the VM. One command, then answer questions.

The equity bot has its **own** broadcaster and its own guide: [`client_agent/DEPLOY.md`](../client_agent/DEPLOY.md). Both bots can run on the same VM, since they are separate services.

---

## Part 1 — FnO broadcaster on Railway (technical)

The FnO broadcaster is a **separate Railway service** running the same repo (`main.py`), configured only for option channels.

1. **Create the service.** In the Railway project: *New → GitHub Repo →* `prshntsingh/darvas_script`, branch `main`. Use the same start command as the equity service, or duplicate the equity service.
2. **Variables** (Railway → service → *Variables*):

| Variable | Value |
|---|---|
| `CHANNEL_MAPPINGS` | The option channels, each with `"enable_fno_trading": true` and `"enable_trading": false` |
| `WS_AUTH_TOKEN` | A long random password, **different** from the equity server's. The bot needs it (Part 2) |
| `TG_API_ID`, `TG_API_HASH` | Same as the equity server |
| `TG_SESSION_STRING` | A **new** session, not the equity server's (see below) |
| `VERTEX_PROJECT_ID`, `VERTEX_CREDENTIALS_JSON` | Gemini fallback for option messages the regex can't read |
| `FNO_MAX_SIGNAL_AGE_SEC` | Optional, default `120`. Option signals older than this are never sent, which protects against replaying old messages after a restart |

`CHANNEL_MAPPINGS` example:
```json
[{"telegram_channel_id": -1009876543210, "label": "options", "enable_trading": false, "enable_fno_trading": true}]
```

**Give this server its own Telegram session.** If both broadcasters use the same `TG_SESSION_STRING` at the same time, Telegram sees one login running in two places and can log out both. Create a new one on your computer:
```bash
python generate_string_session.py   # log in, copy the printed string into this server's TG_SESSION_STRING
```

**Channels on both servers post twice.** Each server posts every message it sees to Discord, Notion and Sheets. If a channel is listed in both servers' `CHANNEL_MAPPINGS`, leave these empty on the FnO server so they aren't posted twice:
- `discord_webhook_url`
- `NOTION_API_KEY`
- `GOOGLE_SHEET_ID`

**Check the broadcaster:**
- **Health:** open `https://<fno-service>.up.railway.app/health`. It should say healthy.
- **Logs:** a parsed option call shows up as `[⚡ FNO] POLYCAB 8000PE OCT @ 90-105 SL 30 T 500,1000`.
- **Address for the bot:** `wss://<fno-service>.up.railway.app/ws`. Give this and the `WS_AUTH_TOKEN` to whoever runs Part 2.

---

## Part 2 — Options bot on the VM (anyone can do this)

### Keep these ready
- **Options server address** (`wss://…/ws`) and **options server password**, from Part 1
- **Dhan Client ID**, **PIN**, **TOTP secret**. If the equity bot is already set up on this VM, these are filled in for you.
- **Rupees per option trade**. The bot buys as many whole lots as fit: with ₹50,000 and a lot of 125 × ₹105 = ₹13,125, it buys 3 lots.
- Optional: a **Telegram bot token** and **chat id**, to get a message for every trade.
- The VM's **static IP whitelisted in Dhan**. This is already done if the equity bot runs on the same VM.

### Setup (about 5 minutes)
1. Open the Google Cloud Console → **Compute Engine → VM instances**. Click **SSH** next to the VM.
2. Paste this line and press Enter:
   ```bash
   cd ~/darvas_script && git pull && bash fno_agent/deploy/setup.sh
   ```
   On a new VM without the folder, use:
   ```bash
   git clone https://github.com/prshntsingh/darvas_script.git ~/darvas_script && bash ~/darvas_script/fno_agent/deploy/setup.sh
   ```
3. Answer the questions. Use the **options** server's address and password here, not the equity one. For "Maximum lots per trade", **1** is safest while you're testing.
4. At the end you should see a green line: **Options (FnO) bot is running and connected — TEST mode**.

### Test first, then go live
The bot starts in **TEST mode**: it receives option signals and shows the contract, lots and orders it *would* place, without placing them.
1. For a day or two, check `bot today fno` after signals arrive.
2. **First live trade:** keep maximum lots at 1, run `bot live fno` and type `YES`. After the first order fills, open the Dhan app and check that it shows the **target and stop-loss legs**.
3. Then raise the lot limit with `bot settings fno` (`FNO_MAX_LOTS=0` means no limit).
4. To go back to test mode at any time: `bot test fno`.

### Everyday commands
| Type this | What it does |
|---|---|
| `bot status` | Is it running? TEST or LIVE? |
| `bot today fno` | Today's option signals, orders, skips and errors |
| `bot logs fno` | Watch live (press **Ctrl+C** to stop watching; the bot keeps running) |
| `bot restart fno` | Restart. Fixes most problems. Trades in progress resume automatically |
| `bot settings fno` | Change a setting, e.g. `FNO_CAPITAL_PER_TRADE`. Save with **Ctrl+O**, Enter, then **Ctrl+X** |
| `bot setup fno` | Answer the setup questions again |
| `bot update` | Install the newest version (outside market hours) |

### What runs automatically
- **Start and restart:** the bot starts when the VM starts, and restarts within 10 seconds if it crashes.
- **Daily refresh:** at 08:00 IST a fresh Dhan login; at 08:30 IST today's contract list (lot sizes, expiries).
- **Protection at Dhan:** stop-loss and targets are placed at Dhan with each entry, so they still work if the VM is down.
- **Crash recovery:** after a restart, any trade in progress is picked up again. Its fill is checked and the protection is completed.
- **Market hours:** new entries only 09:15–15:25 IST on weekdays, skipping the dates in `fno_agent/holidays.json` (**update this every year**).

### If something looks wrong
| You see | Do this |
|---|---|
| `NOT RUNNING` in `bot status` | `bot restart fno`, then `bot logs fno` |
| Setup says "did not connect" | Wrong options server address or password: `bot setup fno`. Also check the FnO broadcaster's `/health` |
| `Skipped … budget` | One lot costs more than your per-trade rupees. Raise `FNO_CAPITAL_PER_TRADE` (`bot settings fno`) |
| `Skipped … missed entry` | The price had already moved more than 3% above the entry range. This is intended |
| `Skipped … contract not found` | The strike or month in the message doesn't exist on the exchange. Nothing was bought |
| `UNPROTECTED position` | **Place the stop-loss in the Dhan app yourself right away**, then send the logs to the maintainer |
| No option signals at all | Check the FnO broadcaster: the channel needs `"enable_fno_trading": true` |

---

## Technical details
- `fno_agent/deploy/setup.sh` does the following:
  - installs git, python3-venv and nano
  - sets the timezone to IST and caps journald logs at 500 MB
  - creates the repo `.venv` and installs `fno_agent/requirements.txt`
  - writes `fno_agent/.env` (chmod 600) with `BROKER=dhan` and a blank `DHAN_ACCESS_TOKEN`; other keys are kept
  - installs `fno_agent/deploy/fno-agent.service` (`Restart=always`) and links `/usr/local/bin/bot`
- **Shared Dhan token.** Dhan keeps only one valid token per account, so each login cancels the previous one. Both bots therefore share one token through `~/.dhan_token` (`client_agent/dhan_token.py`):
  - A bot reuses the shared token on start.
  - When Dhan rejects a token (`DH-906 Invalid Token`), the bot takes the other bot's newer token from the file, and only logs in again if there isn't one.
  - The options bot's 08:00 login is the one new login per day, and the equity bot picks it up at its 08:45 restart.
- **Other settings** (`bot settings fno`):
  - `FNO_CHASE_PCT` (default 3)
  - `FNO_ENTRY_TIMEOUT_SEC` (default 300)
  - `FNO_MIN_DAYS_TO_EXPIRY` (default 1)
  - `ALLOWED_CHANNELS`
- **Journal**: `sqlite3 fno_agent/fno_journal.db "select signal_id,status,reason,filled_qty from signals order by received_at desc limit 20"`
- **Log files**: `fno_agent/logs/fno_agent.log` (rotating) and `journalctl -u fno-agent`.
