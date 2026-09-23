#!/usr/bin/env bash
# One-time setup of the trading bots on a Google Cloud VM (Debian/Ubuntu).
# Safe to run again at any time: it keeps existing settings and only fills in what you change.
#
#   bash deploy/setup_vm.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO_DIR/deploy/lib.sh"

if [ "$(id -u)" = "0" ]; then
    fail "Please run this WITHOUT sudo:  bash deploy/setup_vm.sh"
    exit 1
fi
RUN_USER="$(whoami)"
cd "$REPO_DIR"

# ask VAR "Question" "default" [secret]
ask() {
    local __var=$1 question=$2 default=${3:-} secret=${4:-} answer shown
    if [ -n "$default" ]; then
        if [ "$secret" = secret ]; then shown="press Enter to keep current"; else shown="$default"; fi
        question="$question [$shown]"
    fi
    if [ "$secret" = secret ]; then read -r -s -p "  $question: " answer; echo; else read -r -p "  $question: " answer; fi
    printf -v "$__var" '%s' "${answer:-$default}"
}

# ask_required: same as ask, but repeats until something is entered
ask_required() {
    while true; do
        ask "$@"
        [ -n "${!1}" ] && return 0
        warn "This one is required."
    done
}

yes_no() {  # yes_no "Question" default(y/n)
    local answer
    read -r -p "  $1 [$( [ "$2" = y ] && echo Y/n || echo y/N )]: " answer
    answer=${answer:-$2}
    [[ "$answer" =~ ^[Yy] ]]
}

bold "=== Trading bots: one-time setup ==="
echo "Folder: $REPO_DIR   User: $RUN_USER"
echo

# ---------------------------------------------------------------- system
bold "Step 1/5: Installing system packages (takes a minute)..."
sudo apt-get update -qq
sudo apt-get install -y -qq git python3-venv python3-pip nano >/dev/null
sudo timedatectl set-timezone Asia/Kolkata || true
sudo mkdir -p /etc/systemd/journald.conf.d
printf "[Journal]\nSystemMaxUse=500M\n" | sudo tee /etc/systemd/journald.conf.d/size.conf >/dev/null
sudo systemctl restart systemd-journald
ok "System ready (timezone: India)"

bold "Step 2/5: Installing Python libraries..."
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r client_agent/requirements.txt -r fno_agent/requirements.txt
ok "Python libraries installed"

# ---------------------------------------------------------------- settings
EQ_ENV="$(env_of equity)"; FNO_ENV="$(env_of fno)"
[ -f "$EQ_ENV" ]  || cp client_agent/.env.example "$EQ_ENV"
[ -f "$FNO_ENV" ] || cp fno_agent/.env.example "$FNO_ENV"
chmod 600 "$EQ_ENV" "$FNO_ENV"

echo
bold "Step 3/5: Your settings (press Enter to keep the value shown in [brackets])"
echo
echo " Which bots do you want to run?"
yes_no "Run the EQUITY (shares) bot?" y && WANT_EQ=1 || WANT_EQ=0
yes_no "Run the OPTIONS (FnO) bot?" "$(is_installed fno && echo y || echo n)" && WANT_FNO=1 || WANT_FNO=0
if [ "$WANT_EQ$WANT_FNO" = "00" ]; then warn "No bot selected. Nothing to do."; exit 0; fi

echo
echo " Signal server (from whoever runs the Railway server):"
ask_required WS_URL "Server address (starts with wss://)" "$(get_env WS_SERVER_URL "$EQ_ENV" || true)"
ask WS_TOKEN "Server password (WS_AUTH_TOKEN)"      "$(get_env WS_AUTH_TOKEN "$EQ_ENV" || true)" secret

echo
echo " Dhan account (Dhan web → My Profile → DhanHQ Trading APIs):"
ask_required DHAN_ID "Dhan Client ID"              "$(get_env DHAN_CLIENT_ID "$EQ_ENV" || true)"
ask_required DHAN_PIN "Dhan login PIN"              "$(get_env DHAN_PIN "$EQ_ENV" || true)" secret
ask_required DHAN_TOTP "Dhan TOTP secret (the long code shown when you enabled TOTP)" "$(get_env DHAN_TOTP_SECRET "$EQ_ENV" || true)" secret

for f in "$EQ_ENV" "$FNO_ENV"; do
    set_env WS_SERVER_URL "$WS_URL" "$f"
    set_env WS_AUTH_TOKEN "$WS_TOKEN" "$f"
    set_env BROKER dhan "$f"
    set_env DHAN_CLIENT_ID "$DHAN_ID" "$f"
    set_env DHAN_ACCESS_TOKEN "" "$f"   # blank = log in automatically with PIN + TOTP every day
    set_env DHAN_PIN "$DHAN_PIN" "$f"
    set_env DHAN_TOTP_SECRET "$DHAN_TOTP" "$f"
    [ -n "$(get_env DRY_RUN "$f")" ] || set_env DRY_RUN true "$f"
done

if [ "$WANT_EQ" = 1 ]; then
    echo
    echo " Equity bot:"
    ask_required EQ_AMT "Rupees to invest per share trade" "$(get_env TRADE_AMOUNT_INR "$EQ_ENV" || true)"
    set_env TRADE_AMOUNT_INR "$EQ_AMT" "$EQ_ENV"
fi

if [ "$WANT_FNO" = 1 ]; then
    echo
    echo " Options bot:"
    ask_required FNO_AMT "Rupees to use per option trade (bot buys as many lots as fit)" "$(get_env FNO_CAPITAL_PER_TRADE "$FNO_ENV" || true)"
    ask FNO_MAXL "Maximum lots per trade (0 = no limit)" "$(x=$(get_env FNO_MAX_LOTS "$FNO_ENV"); echo "${x:-0}")"
    echo "  Optional: get trade alerts on Telegram (press Enter to skip)."
    ask TG_TOKEN "Telegram bot token (from @BotFather)" "$(get_env FNO_TG_BOT_TOKEN "$FNO_ENV" || true)" secret
    ask TG_CHAT  "Telegram chat id (from @userinfobot)"  "$(get_env FNO_TG_CHAT_ID "$FNO_ENV" || true)"
    set_env FNO_CAPITAL_PER_TRADE "$FNO_AMT" "$FNO_ENV"
    set_env FNO_MAX_LOTS "${FNO_MAXL:-0}" "$FNO_ENV"
    set_env FNO_TG_BOT_TOKEN "$TG_TOKEN" "$FNO_ENV"
    set_env FNO_TG_CHAT_ID "$TG_CHAT" "$FNO_ENV"
fi
ok "Settings saved"

# ---------------------------------------------------------------- services
echo
bold "Step 4/5: Setting the bots to run 24/7 (auto-restart, start on boot)..."
install_unit() {  # install_unit deploy/file.service
    sed -e "s|/home/YOUR_USER/darvas_script|$REPO_DIR|g" -e "s|YOUR_USER|$RUN_USER|g" "$1" \
        | sudo tee "/etc/systemd/system/$(basename "$1")" >/dev/null
}
install_unit deploy/trading-client.service
install_unit deploy/fno-agent.service
sudo cp deploy/trading-client-restart.service deploy/trading-client-restart.timer /etc/systemd/system/
sudo systemctl daemon-reload

if [ "$WANT_EQ" = 1 ]; then
    sudo systemctl enable -q trading-client
    sudo systemctl enable -q --now trading-client-restart.timer
else
    sudo systemctl disable -q --now trading-client trading-client-restart.timer 2>/dev/null || true
fi
if [ "$WANT_FNO" = 1 ]; then
    sudo systemctl enable -q fno-agent
else
    sudo systemctl disable -q --now fno-agent 2>/dev/null || true
fi

# `bot` command available from anywhere
sudo ln -sf "$REPO_DIR/bot" /usr/local/bin/bot
chmod +x "$REPO_DIR/bot"
ok "Installed. The 'bot' command is ready."

# ---------------------------------------------------------------- start + check
echo
bold "Step 5/5: Starting the bots and checking they connect..."
wait_for() {  # wait_for BOT "log text" seconds
    local svc; svc=$(svc_of "$1")
    local since; since=$(date '+%Y-%m-%d %H:%M:%S')
    sudo systemctl restart "$svc"
    for _ in $(seq "$3"); do
        if sudo journalctl -u "$svc" --since "$since" --no-pager 2>/dev/null | grep -F "$2" >/dev/null; then
            ok "$(label_of "$1") is running and connected — $(mode_of "$1")"
            return 0
        fi
        sleep 1
    done
    fail "$(label_of "$1") did not connect within $3 seconds. Last messages:"
    sudo journalctl -u "$svc" --since "$since" --no-pager -n 15 | cut -c1-200
    return 1
}
status=0
[ "$WANT_EQ" = 1 ]  && { wait_for equity "Connected to broadcaster" 60 || status=1; }
[ "$WANT_FNO" = 1 ] && { wait_for fno    "Connected to broadcaster" 120 || status=1; }

echo
if [ $status = 0 ]; then
    bold "All done! The bots are running (test/live mode shown above)."
else
    bold "Setup finished, but something needs attention (see above). Fix it with:  bot settings equity|fno"
fi
cat <<EOF

Everyday commands (type them in this window):
  bot status          is everything running?
  bot logs            watch what the bots are doing (Ctrl+C to stop watching)
  bot live equity     start placing REAL share orders    (bot test equity = back to test mode)
  bot live fno        start placing REAL option orders   (bot test fno    = back to test mode)
  bot help            all commands
EOF
