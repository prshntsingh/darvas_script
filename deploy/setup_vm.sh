#!/usr/bin/env bash
# Convenience wrapper: set up one or both bots on the VM.
# Each bot also has its own setup script:
#   bash client_agent/deploy/setup.sh     (equity bot)   see client_agent/DEPLOY.md
#   bash fno_agent/deploy/setup.sh        (options bot)  see fno_agent/DEPLOY.md
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO_DIR/deploy/lib.sh"
require_not_root deploy/setup_vm.sh

yes_no() {  # yes_no "Question" default(y/n)
    local answer
    read -r -p "  $1 [$( [ "$2" = y ] && echo Y/n || echo y/N )]: " answer
    answer=${answer:-$2}
    [[ "$answer" =~ ^[Yy] ]]
}

bold "Which bots do you want to set up on this VM?"
status=0; any=0
if yes_no "Set up the EQUITY (shares) bot?" y; then
    any=1; echo; bash "$(setup_of equity)" || status=1; echo
fi
if yes_no "Set up the OPTIONS (FnO) bot?" "$(is_installed fno && echo y || echo n)"; then
    any=1; echo; bash "$(setup_of fno)" || status=1; echo
fi
[ $any = 1 ] || warn "Nothing selected."
[ $any = 1 ] && command -v bot >/dev/null && bot status
exit $status
