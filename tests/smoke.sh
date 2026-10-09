#!/usr/bin/env bash
# Smoke verification on an ISOLATED disposable Ubuntu VM only; disruptive tests opt in.
set -Eeuo pipefail
PASS=0
FAIL=0
SKIP=0
report() { local status=$1 label=$2; echo "$status: $label"; case "$status" in PASS) PASS=$((PASS+1));; FAIL) FAIL=$((FAIL+1));; "NOT RUN") SKIP=$((SKIP+1));; esac; }
testcmd() { local label=$1; shift; if "$@"; then report PASS "$label"; else report FAIL "$label"; fi; }
echo 'WireDash smoke (run only in throwaway VM; --destructive enables induced hangs)'
testcmd "Bash syntax installer" bash -n install.sh
testcmd "Bash syntax backup" bash -n deploy/wgdashbackup.sh
testcmd "Bash syntax watchdog" bash -n deploy/wgd-watchdog.sh
testcmd "AST getLink patch" python3 tests/check_patch.py
if command -v shellcheck >/dev/null; then
  testcmd "shellcheck helper scripts" shellcheck install.sh deploy/wgdashbackup.sh deploy/wgd-watchdog.sh
else report "NOT RUN" "shellcheck not installed"; fi
if docker compose version >/dev/null 2>&1; then
  testcmd "Docker Compose configuration" env WGD_PORT=10086 docker compose -f deploy/compose.yaml config -q
else report "NOT RUN" "Docker Compose missing"; fi
if docker inspect wgdashboard >/dev/null 2>&1; then
  testcmd "HTTP health" curl -fsS --max-time 5 http://127.0.0.1:10086/api/handshake
  testcmd "Source patch in installed container" docker exec -i wgdashboard python3 - /opt/wgdashboard/src/modules/PeerShareLinks.py < tests/check_patch.py
else report "NOT RUN" "container missing"; fi
if [[ "${1:-}" == --destructive ]]; then
  echo 'Disruptive hang/recovery and live VPN continuity need a monitored connected client.'
  report "NOT RUN" 'T2/T3/T4/T5/T6 require supervised external client and manual testing'
else
  report "NOT RUN" 'T2/T3/T4/T5/T6 disruptive recovery checks skipped (pass --destructive for instructions)'
fi
echo "Summary: PASS=$PASS FAIL=$FAIL NOT_RUN=$SKIP"
((FAIL==0))
