#!/usr/bin/env bash
# Set up (or change the settings of) the OPTIONS (FnO) bot on the VM. Safe to run again any time.
#
#   bash fno_agent/deploy/setup.sh        (or: bot setup fno)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$REPO_DIR/deploy/lib.sh"
require_not_root fno_agent/deploy/setup.sh
cd "$REPO_DIR"

ENV="$(env_of fno)"; OTHER="$(env_of equity)"

bold "=== Options (FnO) bot setup ==="
echo "Press Enter to keep the value shown in [brackets]. Secret answers stay invisible while you type."
echo

system_setup fno_agent/requirements.txt
if [ ! -f "$ENV" ]; then
    cp fno_agent/.env.example "$ENV"
    set_env FNO_MAX_LOTS 1 "$ENV"   # first setup: suggest 1 lot until the first live trade is checked
fi
chmod 600 "$ENV"

echo
echo " OPTIONS signal server (the Railway broadcaster for option signals — not the equity one):"
ask_server Options "$ENV"
if [ "$WS_URL" = "$(get_env WS_SERVER_URL "$OTHER")" ]; then
    warn "This is the same address the equity bot uses. That's fine only if one server sends both kinds of signals."
fi

echo
ask_dhan "$ENV" "$OTHER"

echo
ask_required AMOUNT "Rupees to use per option trade (the bot buys as many whole lots as fit)" "$(get_env FNO_CAPITAL_PER_TRADE "$ENV")"
ask MAXLOTS "Maximum lots per trade (0 = no limit; 1 is safest while testing)" "$(x=$(get_env FNO_MAX_LOTS "$ENV"); echo "${x:-0}")"
echo "  Optional: trade alerts on Telegram (press Enter to skip)."
ask TG_TOKEN "Telegram bot token (from @BotFather)" "$(get_env FNO_TG_BOT_TOKEN "$ENV")" secret
ask TG_CHAT  "Telegram chat id (from @userinfobot)"  "$(get_env FNO_TG_CHAT_ID "$ENV")"
set_env FNO_CAPITAL_PER_TRADE "$AMOUNT" "$ENV"
set_env FNO_MAX_LOTS "${MAXLOTS:-0}" "$ENV"
set_env FNO_TG_BOT_TOKEN "$TG_TOKEN" "$ENV"
set_env FNO_TG_CHAT_ID "$TG_CHAT" "$ENV"
[ -n "$(get_env DRY_RUN "$ENV")" ] || set_env DRY_RUN true "$ENV"
ok "Settings saved to fno_agent/.env"

echo
bold "Installing the options bot as a 24/7 service..."
install_unit fno_agent/deploy/fno-agent.service
sudo systemctl daemon-reload
sudo systemctl enable -q fno-agent
ok "Starts on boot, restarts if it crashes, refreshes its Dhan login and contract list every morning"

echo
bold "Starting the options bot and checking it connects (downloads the contract list first, up to 2 min)..."
if start_and_check fno 120; then
    echo
    bold "Done! Next steps:"
    echo "  bot today fno        see signals and orders (TEST mode shows what it WOULD buy)"
    echo "  bot live fno         when you're happy, start placing REAL option orders"
else
    echo
    bold "Something needs attention (see above). Most often the server address or password is wrong:"
    echo "  bot setup fno        re-enter the settings"
    exit 1
fi
