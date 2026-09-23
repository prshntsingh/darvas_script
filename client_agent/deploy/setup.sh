#!/usr/bin/env bash
# Set up (or change the settings of) the EQUITY bot on the VM. Safe to run again any time.
#
#   bash client_agent/deploy/setup.sh        (or: bot setup equity)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$REPO_DIR/deploy/lib.sh"
require_not_root client_agent/deploy/setup.sh
cd "$REPO_DIR"

ENV="$(env_of equity)"; OTHER="$(env_of fno)"

bold "=== Equity bot setup ==="
echo "Press Enter to keep the value shown in [brackets]. Secret answers stay invisible while you type."
echo

system_setup client_agent/requirements.txt
[ -f "$ENV" ] || cp client_agent/.env.example "$ENV"
chmod 600 "$ENV"

echo
echo " EQUITY signal server (the Railway broadcaster for share signals — not the options one):"
ask_server Equity "$ENV"

echo
ask_dhan "$ENV" "$OTHER"

echo
ask_required AMOUNT "Rupees to invest per share trade" "$(get_env TRADE_AMOUNT_INR "$ENV")"
set_env TRADE_AMOUNT_INR "$AMOUNT" "$ENV"
[ -n "$(get_env DRY_RUN "$ENV")" ] || set_env DRY_RUN true "$ENV"
ok "Settings saved to client_agent/.env"

echo
bold "Installing the equity bot as a 24/7 service..."
install_unit client_agent/deploy/trading-client.service
sudo cp client_agent/deploy/trading-client-restart.service client_agent/deploy/trading-client-restart.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable -q trading-client
sudo systemctl enable -q --now trading-client-restart.timer
ok "Starts on boot, restarts if it crashes, fresh restart every weekday at 08:45 IST"

echo
bold "Starting the equity bot and checking it connects..."
if start_and_check equity 60; then
    echo
    bold "Done! Next steps:"
    echo "  bot today equity     see signals and orders (TEST mode shows what it WOULD buy)"
    echo "  bot live equity      when you're happy, start placing REAL share orders"
else
    echo
    bold "Something needs attention (see above). Most often the server address or password is wrong:"
    echo "  bot setup equity     re-enter the settings"
    exit 1
fi
