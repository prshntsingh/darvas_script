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
   - A message is dropped unless it has SL < entry < targets.
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
   5. **Price check**: skip if LTP is more than `FNO_CHASE_PCT` above the entry range, or already at the SL.
   6. **Entry**: a LIMIT buy at `min(entry_max, LTP + 2 ticks)`. It never uses a market order, because many options are illiquid. Anything unfilled after `FNO_ENTRY_TIMEOUT_SEC` is cancelled.
   7. **Protection at the broker**, so it still works if the VM is down. Lots are split across the targets, and any remainder goes to the furthest target.
      - **Dhan**: one **Super Order** per target tranche, with entry, target leg and SL leg, product `MARGIN` (carry-forward). Dhan Forever/OCO only supports CNC/MTF, so it can't be used for F&O positions carried overnight.
      - **Kite**: one NRML entry, then a **GTT OCO** per tranche after the fill. The SL leg's limit price is `FNO_SL_LIMIT_BUFFER_PCT` below the trigger.
   8. Every step is written to `fno_journal.db`. After a restart, unfinished trades are resumed: the agent checks the fill, cancels on timeout, and places any missing protection.
   9. Every event goes to Telegram (`FNO_TG_BOT_TOKEN`, `FNO_TG_CHAT_ID`), plus a 09:00 heartbeat.

Worked example with a ₹50,000 budget. POLYCAB lot size is 125, so 1 lot = 125 × 105 = ₹13,125, which gives **3 lots**:
- tranche 1: 1 lot, SL 30 / target 500
- tranche 2: 2 lots, SL 30 / target 1000

## Setup

```bash
pip install -r fno_agent/requirements.txt
cp fno_agent/.env.example fno_agent/.env   # fill in credentials
python -m fno_agent.main fno_agent/.env     # DRY_RUN=true by default
```

On the broadcaster, add `"enable_fno_trading": true` to the channel's entry in `CHANNEL_MAPPINGS`.

The equity client (`client_agent/`) ignores FNO signals, so both can run side by side.

### Daily token handling
- **Dhan**: set `DHAN_PIN` and `DHAN_TOTP_SECRET`. The agent logs in with TOTP at 08:00 IST and again whenever it gets a 401.
- **Kite**: a manual login is needed every day. Run `python -m fno_agent.kite_login fno_agent/.env` after 07:30 IST. The agent reloads the token file at 08:00 and whenever a token error occurs, so no restart is needed.

## Running 24/7 on the VM
Follow the simple guide in `client_agent/README.md` → "Running 24/7 on GCP (simple guide)". The same `bash deploy/setup_vm.sh` sets up both bots; answer **Y** to "Run the OPTIONS (FnO) bot?". Then use `bot status`, `bot today fno`, `bot logs fno`, and `bot live fno` / `bot test fno`.

- The agent refreshes its Dhan login (08:00 IST) and instrument list (08:30 IST) by itself, so it needs no daily restart.
- Logs: `bot logs fno`, and also `fno_agent/logs/fno_agent.log` (rotating).
- Update `holidays.json` every year from the NSE holiday list.
- If you set up Telegram alerts and there's no 09:00 heartbeat on a weekday, run `bot status`.

## Before going live
1. Run with `DRY_RUN=true` and check the logged payloads and Telegram messages for a few real signals.
2. Set `FNO_MAX_LOTS=1` and a small budget (`bot settings fno`), then `bot live fno`. Let one trade run and confirm in the broker app that:
   - **Dhan**: the super order shows target and SL legs on the `MARGIN` product.
   - **Kite**: the GTT OCO appears under GTT.
3. Then raise the limits.

Useful journal queries:
```bash
sqlite3 fno_agent/fno_journal.db "select signal_id,status,reason,filled_qty from signals order by received_at desc limit 20"
```
