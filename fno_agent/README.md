# FnO Agent

Trades option calls posted on Telegram, such as:

```
#POLYCAB 8000 PE OCT @90-105
SL-30
Target-500,1000
```

## Flow

1. **Broadcaster (`main.py`)**: channels with `"enable_fno_trading": true` in `CHANNEL_MAPPINGS` go through `services/fno_parser.py`.
   - Parsing is regex first, with Gemini as a fallback.
   - Only BUY entries are produced. Messages about exits or booking profit are ignored.
   - Stop-loss and targets in the message are optional. The bot sets its own (see step 7), so a call like `#LT 3900PE @80` is enough.
   - Messages older than `FNO_MAX_SIGNAL_AGE_SEC` (default 120) are never broadcast. This stops the startup catch-up from replaying old calls.
   - Valid signals are broadcast with `asset_class: "FNO"` and a unique `signal_id`.
2. **Agent (`python -m fno_agent.main`)** runs these steps in order:
   1. **Dedup** on `signal_id` using the SQLite journal.
   2. **Market-hours gate** (09:15–15:25 IST). Also skips weekends and the dates in `holidays.json`.
   3. **Contract resolution** from the broker's instrument master:
      - A named month means that month's monthly expiry. If it isn't found, the trade is rejected.
      - With no month, it takes the nearest expiry at least `FNO_MIN_DAYS_TO_EXPIRY` days away.
      - SENSEX, BANKEX and SENSEX50 go to BFO; everything else to NFO.
   4. **Sizing**: `lots = floor(FNO_CAPITAL_PER_TRADE / (lot_size × entry_max))`. If that is 0 lots, the trade is skipped.
   5. **Price check**: skip if LTP is more than `FNO_CHASE_PCT` above the entry range.
   6. **Entry**: a LIMIT buy at `min(entry_max, LTP + 2 ticks)`. It never uses a market order, because many options are illiquid. Anything unfilled after `FNO_ENTRY_TIMEOUT_SEC` is cancelled.
   7. **Protection at the broker** (optional, on by default via `FNO_SL_TARGET_ENABLED`), so it still works if the VM is down.
      - **Levels**: stop-loss = entry − `FNO_SL_PCT`% and one target = entry + `FNO_TARGET_PCT`%, both **3% by default**. The entry is the LIMIT price sent. SL rounds down and target rounds up to the tick. The message's own SL/targets are ignored.
      - **Dhan**: one **Super Order** (entry + target leg + SL leg) for the whole quantity, product `MARGIN` (carry-forward). Dhan Forever/OCO only supports CNC/MTF, so it can't be used for F&O positions carried overnight.
      - **Kite**: one NRML entry, then one **GTT OCO** after the fill. The SL leg's limit price is `FNO_SL_LIMIT_BUFFER_PCT` below the trigger.
      - When it's turned off, the bot places one plain LIMIT buy and no SL/target orders.
   8. Every step is written to `fno_journal.db`. After a restart, unfinished trades are resumed: the agent checks the fill, cancels on timeout, and places any missing protection.
   9. Every event goes to Telegram (`FNO_TG_BOT_TOKEN`, `FNO_TG_CHAT_ID`), plus a 09:00 heartbeat.

Worked example with a ₹50,000 budget. POLYCAB lot size is 125, so 1 lot = 125 × 105 = ₹13,125, which gives **3 lots**:
- LIMIT buy at ₹105 (if the price is at the top of the range)
- stop-loss ₹101.85 (−3%), target ₹108.15 (+3%), placed at Dhan with the entry

## Setup

```bash
pip install -r fno_agent/requirements.txt
cp fno_agent/.env.example fno_agent/.env   # fill in credentials
python -m fno_agent.main fno_agent/.env     # DRY_RUN=true by default
```

On the broadcaster, add `"enable_fno_trading": true` to the channel's entry in `CHANNEL_MAPPINGS`.

The equity client (`client_agent/`) ignores FNO signals, so both can run side by side.

### Key settings (`fno_agent/.env`, or `bot settings fno` on the VM)

| Setting | Default | Meaning |
|---|---|---|
| `FNO_CAPITAL_PER_TRADE` | — | Rupees per trade. The bot buys as many whole lots as fit |
| `FNO_MAX_LOTS` | `0` | Cap on lots per trade (`0` = no cap; the VM setup suggests `1` while testing) |
| `FNO_SL_TARGET_ENABLED` | `true` | Place a stop-loss and target at the broker. `false` = one plain buy, and you manage exits yourself |
| `FNO_SL_PCT` | `3` | Stop-loss % below the entry price |
| `FNO_TARGET_PCT` | `3` | Target % above the entry price |
| `FNO_CHASE_PCT` | `3` | Skip if the price is already this % above the entry range |
| `FNO_ENTRY_TIMEOUT_SEC` | `300` | Cancel an unfilled entry after this long |
| `FNO_MIN_DAYS_TO_EXPIRY` | `1` | Never buy a contract expiring sooner than this |
| `DRY_RUN` | `true` | `true` = log the orders without placing them |

### Daily token handling
- **Dhan**: set `DHAN_PIN` and `DHAN_TOTP_SECRET`. The agent logs in with TOTP at 08:00 IST. It shares that token with the equity bot through `~/.dhan_token`, so they don't log each other out. On a rejected token it adopts the shared one, or logs in again.
- **Kite**: a manual login is needed every day. Run `python -m fno_agent.kite_login fno_agent/.env` after 07:30 IST. The agent reloads the token file at 08:00 and whenever a token error occurs, so no restart is needed.

## Deploying (Railway broadcaster + VM)

See **[DEPLOY.md](DEPLOY.md)**. It covers:
- the separate FnO broadcaster on Railway
- the one-command setup of this bot on the VM (`bash fno_agent/deploy/setup.sh`)
- everyday `bot` commands
- the safe path to going live

Useful journal queries:
```bash
sqlite3 fno_agent/fno_journal.db "select signal_id,status,reason,filled_qty from signals order by received_at desc limit 20"
```
