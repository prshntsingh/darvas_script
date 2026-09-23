# Shared helpers for the agent setup scripts and ./bot  (sourced, not executed)
# Expects REPO_DIR to be set by the caller.

EQUITY_SVC=trading-client
FNO_SVC=fno-agent

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
ok()    { printf '\033[32m✔ %s\033[0m\n' "$*"; }
warn()  { printf '\033[33m! %s\033[0m\n' "$*" >&2; }
fail()  { printf '\033[31m✘ %s\033[0m\n' "$*" >&2; }

# bot name (equity|fno) -> systemd service / .env file / friendly label / setup script
svc_of()   { case "$1" in equity) echo "$EQUITY_SVC" ;; fno) echo "$FNO_SVC" ;; esac; }
env_of()   { case "$1" in equity) echo "$REPO_DIR/client_agent/.env" ;; fno) echo "$REPO_DIR/fno_agent/.env" ;; esac; }
label_of() { case "$1" in equity) echo "Equity bot" ;; fno) echo "Options (FnO) bot" ;; esac; }
setup_of() { case "$1" in equity) echo "$REPO_DIR/client_agent/deploy/setup.sh" ;; fno) echo "$REPO_DIR/fno_agent/deploy/setup.sh" ;; esac; }

is_installed() { systemctl cat "$(svc_of "$1").service" >/dev/null 2>&1; }

# get_env KEY FILE -> value (inline "# comments" and quotes stripped; "your_..." placeholders treated as empty)
get_env() {
    [ -f "$2" ] || return 0
    local v
    v=$(grep -E "^[[:space:]]*$1[[:space:]]*=" "$2" | tail -n1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/^[[:space:]]+//; s/[[:space:]]+$//; s/^"(.*)"$/\1/' || true)
    case "$v" in your*|YOUR*) v="" ;; esac
    printf '%s' "$v"
}

# first_set VALUE... -> the first non-empty value (for defaults taken from the other agent's .env)
first_set() { local v; for v in "$@"; do [ -n "$v" ] && { printf '%s' "$v"; return; }; done; }

# set_env KEY VALUE FILE  (replaces the existing line or appends one; keeps everything else)
set_env() {
    python3 - "$3" "$1" "$2" <<'PY'
import re, sys
path, key, val = sys.argv[1:]
lines = open(path).read().splitlines()
out, done = [], False
for line in lines:
    if re.match(r"^\s*" + re.escape(key) + r"\s*=", line):
        if not done:
            out.append(f"{key}={val}")
            done = True
        continue
    out.append(line)
if not done:
    out.append(f"{key}={val}")
open(path, "w").write("\n".join(out) + "\n")
PY
}

# mode_of BOT -> "TEST mode" / "LIVE"
mode_of() {
    case "$(get_env DRY_RUN "$(env_of "$1")" | tr '[:upper:]' '[:lower:]')" in
        false|0|no) echo "LIVE (real orders)" ;;
        *) echo "TEST mode (no real orders)" ;;
    esac
}

# ---------------------------------------------------------------- setup-script helpers

require_not_root() {
    if [ "$(id -u)" = "0" ]; then
        fail "Please run this WITHOUT sudo:  bash $1"
        exit 1
    fi
}

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

# system_setup REQUIREMENTS_FILE...  (idempotent: fast when already done)
system_setup() {
    bold "Preparing the VM (first time takes a minute)..."
    if ! command -v git >/dev/null || ! python3 -m venv --help >/dev/null 2>&1 || ! command -v nano >/dev/null; then
        sudo apt-get update -qq
        sudo apt-get install -y -qq git python3-venv python3-pip nano >/dev/null
    fi
    sudo timedatectl set-timezone Asia/Kolkata || true
    if [ ! -f /etc/systemd/journald.conf.d/size.conf ]; then
        sudo mkdir -p /etc/systemd/journald.conf.d
        printf "[Journal]\nSystemMaxUse=500M\n" | sudo tee /etc/systemd/journald.conf.d/size.conf >/dev/null
        sudo systemctl restart systemd-journald
    fi
    [ -d "$REPO_DIR/.venv" ] || python3 -m venv "$REPO_DIR/.venv"
    "$REPO_DIR/.venv/bin/pip" install -q --upgrade pip
    local req; for req in "$@"; do "$REPO_DIR/.venv/bin/pip" install -q -r "$req"; done
    # `bot` command available from anywhere
    sudo ln -sf "$REPO_DIR/bot" /usr/local/bin/bot
    chmod +x "$REPO_DIR/bot"
    ok "VM ready (timezone: India, Python libraries installed, 'bot' command available)"
}

# ask_dhan ENV_FILE OTHER_ENV_FILE  (defaults from this agent's .env, else the other agent's)
ask_dhan() {
    local own=$1 other=$2
    echo " Dhan account (Dhan web → My Profile → DhanHQ Trading APIs):"
    ask_required DHAN_ID   "Dhan Client ID" "$(first_set "$(get_env DHAN_CLIENT_ID "$own")" "$(get_env DHAN_CLIENT_ID "$other")")"
    ask_required DHAN_PIN  "Dhan login PIN" "$(first_set "$(get_env DHAN_PIN "$own")" "$(get_env DHAN_PIN "$other")")" secret
    ask_required DHAN_TOTP "Dhan TOTP secret (the long code shown when you enabled TOTP)" \
        "$(first_set "$(get_env DHAN_TOTP_SECRET "$own")" "$(get_env DHAN_TOTP_SECRET "$other")")" secret
    set_env BROKER dhan "$own"
    set_env DHAN_CLIENT_ID "$DHAN_ID" "$own"
    set_env DHAN_ACCESS_TOKEN "" "$own"   # blank = log in automatically with PIN + TOTP
    set_env DHAN_PIN "$DHAN_PIN" "$own"
    set_env DHAN_TOTP_SECRET "$DHAN_TOTP" "$own"
}

# install_unit FILE  (fills in the user and repo path, copies to /etc/systemd/system)
install_unit() {
    sed -e "s|/home/YOUR_USER/darvas_script|$REPO_DIR|g" -e "s|YOUR_USER|$(whoami)|g" "$1" \
        | sudo tee "/etc/systemd/system/$(basename "$1")" >/dev/null
}

# start_and_check BOT SECONDS  (restart the service and wait for "Connected to broadcaster")
start_and_check() {
    local svc since; svc=$(svc_of "$1"); since=$(date '+%Y-%m-%d %H:%M:%S')
    sudo systemctl restart "$svc"
    for _ in $(seq "$2"); do
        # grep without -q: reading all input avoids SIGPIPE false negatives under pipefail
        if sudo journalctl -u "$svc" --since "$since" --no-pager 2>/dev/null | grep -F "Connected to broadcaster" >/dev/null; then
            ok "$(label_of "$1") is running and connected — $(mode_of "$1")"
            return 0
        fi
        sleep 1
    done
    fail "$(label_of "$1") did not connect within $2 seconds. Last messages:"
    sudo journalctl -u "$svc" --since "$since" --no-pager -n 15 | cut -c1-200
    return 1
}
