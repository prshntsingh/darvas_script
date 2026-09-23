# Shared helpers for deploy/setup_vm.sh and ./bot  (sourced, not executed)

EQUITY_SVC=trading-client
FNO_SVC=fno-agent

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
ok()    { printf '\033[32m✔ %s\033[0m\n' "$*"; }
warn()  { printf '\033[33m! %s\033[0m\n' "$*" >&2; }
fail()  { printf '\033[31m✘ %s\033[0m\n' "$*" >&2; }

# bot name (equity|fno) -> systemd service / .env file / friendly label
svc_of()   { case "$1" in equity) echo "$EQUITY_SVC" ;; fno) echo "$FNO_SVC" ;; esac; }
env_of()   { case "$1" in equity) echo "$REPO_DIR/client_agent/.env" ;; fno) echo "$REPO_DIR/fno_agent/.env" ;; esac; }
label_of() { case "$1" in equity) echo "Equity bot" ;; fno) echo "Options (FnO) bot" ;; esac; }

is_installed() { systemctl cat "$(svc_of "$1").service" >/dev/null 2>&1; }

# get_env KEY FILE -> value (inline "# comments" and quotes stripped; "your_..." placeholders treated as empty)
get_env() {
    [ -f "$2" ] || return 0
    local v
    v=$(grep -E "^[[:space:]]*$1[[:space:]]*=" "$2" | tail -n1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/^[[:space:]]+//; s/[[:space:]]+$//; s/^"(.*)"$/\1/' || true)
    case "$v" in your*|YOUR*) v="" ;; esac
    printf '%s' "$v"
}

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
