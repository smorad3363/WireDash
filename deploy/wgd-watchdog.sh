#!/usr/bin/env bash
# Recover unresponsive Gunicorn only; NEVER stop/restart Docker, WireGuard or other services.
set -Eeuo pipefail
umask 077

PORT=${WGD_PORT:-10086}
INTERVAL=${WGD_WATCHDOG_INTERVAL:-10}
TIMEOUT=${WGD_WATCHDOG_TIMEOUT:-5}
THRESHOLD=${WGD_WATCHDOG_THRESHOLD:-2}
STATE_DIR=/var/lib/wgd-watchdog
CFG=/etc/wgdashbackup/telegram.conf
CONTAINER=wgdashboard
URL="http://127.0.0.1:${PORT}/api/handshake"

log() { printf '[wgd-watchdog] %s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
[[ "$PORT" =~ ^[0-9]+$ && "$INTERVAL" =~ ^[0-9]+$ && "$TIMEOUT" =~ ^[0-9]+$ && "$THRESHOLD" =~ ^[0-9]+$ ]] || { log 'invalid watchdog configuration'; exit 2; }
(( PORT > 0 && PORT <= 65535 && INTERVAL >= 5 && TIMEOUT >= 1 && THRESHOLD >= 1 )) || exit 2
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"
exec 9>"$STATE_DIR/lock"
flock -n 9 || { log 'already running; exiting'; exit 0; }

probe() {
  local code
  code=$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time "$TIMEOUT" "$URL" 2>/dev/null) || return 1
  [[ "$code" == 200 ]]
}
running() {
  [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true)" == true ]]
}
alert() {
  local message=$1 now previous=0 resp
  [[ -s "$CFG" ]] || { log 'Telegram not configured; alert only in journal'; return 0; }
  now=$(date +%s)
  [[ ! -f "$STATE_DIR/last-alert" ]] || read -r previous < "$STATE_DIR/last-alert" || :
  [[ "$previous" =~ ^[0-9]+$ ]] || previous=0
  if ((now-previous < 1800)); then log 'Alert cooldown active'; return 0; fi
  # Root-only config is created by wireback; TOKEN and CHAT are never put on argv.
  # shellcheck source=/dev/null
  source "$CFG"
  [[ -n "${TOKEN:-}" && -n "${CHAT:-}" ]] || return 0
  resp=$(mktemp "$STATE_DIR/.telegram.XXXXXXXX")
  if curl -fsS --max-time 20 --config - -o "$resp" -F "chat_id=$CHAT" --form-string "text=WGDashboard WATCHDOG: $message (host: $(hostname))" <<EOF
url = "https://api.telegram.org/bot${TOKEN}/sendMessage"
EOF
  then
    printf '%s\n' "$now" > "$STATE_DIR/last-alert"
    if grep -Eq '"ok"[[:space:]]*:[[:space:]]*true' "$resp"; then
      log 'Telegram alert delivered'
    else
      log 'Telegram returned an API error'
    fi
  else
    log 'Telegram alert failed'
  fi
  rm -f "$resp"
}
# Rolling 30-minute recovery cap, persisted across watchdog restarts.
cap_allows() {
  local now t
  local -a prior=() retained=()
  now=$(date +%s)
  if [[ -s "$STATE_DIR/recoveries" ]]; then read -r -a prior < "$STATE_DIR/recoveries" || :; fi
  for t in "${prior[@]}"; do
    if [[ "$t" =~ ^[0-9]+$ ]] && (( now >= t && now-t < 1800 )); then retained+=("$t"); fi
  done
  if (( ${#retained[@]} >= 3 )); then return 1; fi
  retained+=("$now")
  printf '%s\n' "${retained[*]}" > "$STATE_DIR/recoveries"
  return 0
}
recover() {
  log 'L1: HUP gunicorn master'
  docker exec "$CONTAINER" sh -c 'test -s /opt/wgdashboard/src/gunicorn.pid && kill -HUP "$(cat /opt/wgdashboard/src/gunicorn.pid)"' || log 'L1 signal failed'
  sleep 15
  if probe; then log 'L1: panel recovered'; return 0; fi

  log 'L2: terminate only gunicorn processes'
  docker exec "$CONTAINER" pkill -TERM gunicorn || :
  sleep 10
  docker exec "$CONTAINER" pkill -KILL gunicorn || :
  docker exec "$CONTAINER" rm -f /opt/wgdashboard/src/gunicorn.pid || { log 'L2: cannot remove stale PID'; return 1; }
  docker exec -d -w /opt/wgdashboard/src "$CONTAINER" /opt/wgdashboard/src/venv/bin/gunicorn --config /opt/wgdashboard/src/gunicorn.conf.py || { log 'L2: launch failed'; return 1; }
  local i
  for ((i=0; i<6; i++)); do
    sleep 5
    if probe; then log 'L2: panel recovered'; return 0; fi
  done
  log 'L3: Gunicorn recovery failed; no container restart attempted'
  return 1
}
log "Started: $URL (every ${INTERVAL}s, threshold ${THRESHOLD}; cap 3/30m)"
failures=0
was_down=0
while true; do
  if ! running; then
    if ((was_down == 0)); then log 'container stopped/missing; Docker restart policy owns recovery'; fi
    was_down=1; failures=0
    sleep "$INTERVAL"
    continue
  fi
  was_down=0
  if probe; then
    ((failures > 0)) && log 'probe OK after failure'
    failures=0
  else
    failures=$((failures+1))
    log "HTTP probe failed ($failures/$THRESHOLD)"
    if ((failures >= THRESHOLD)); then
      failures=0
      if cap_allows; then
        if ! recover; then alert 'Panel still unresponsive after Gunicorn recovery'; fi
      else
        log 'Recovery limit 3/30m reached; alert only'
        alert 'Recovery limit reached; manual intervention needed'
      fi
    fi
  fi
  sleep "$INTERVAL"
done
