#!/usr/bin/env bash
# shellcheck disable=SC2034,SC2046
# Preserved legacy backup implementation: unused legacy locals and intentional
# optional .env tar word splitting (do not rewrite backup/restore routines).
# WGDashboard Docker LIVE backup + Telegram alerts + health monitor — Ubuntu / systemd / SQLite
# v3.1: configurable backup interval; no Docker stop/restart during backup; restore compatibility fixes for quoted paths, 3/4-digit Telegram parts, TAR ./ roots, and verification extraction.
set -Eeuo pipefail
umask 077

BIN=/usr/local/bin/wgdashbackup
CFG_DIR=/etc/wgdashbackup
CFG=$CFG_DIR/telegram.conf
OUT=/var/backups/wgdashboard
LOG=/var/log/wgdashbackup.log
COMPOSE=/opt/wgdashboard/compose.yaml
UNIT=wgdashbackup
CONTAINER=wgdashboard
HEALTH_UNIT=wgdashbackup-health
LAST_OK=$CFG_DIR/last-success.epoch
LAST_FAIL=$CFG_DIR/last-failure.epoch
LAST_ATTEMPT=$CFG_DIR/last-attempt.epoch
LAST_ALERT=$CFG_DIR/last-alert.epoch
RETRY_STATE=$CFG_DIR/retry-state
SCHEDULE=$CFG_DIR/interval-minutes
RESTORE_JOB_ID=""
RESTORE_PROGRESS_SCRIPT=/usr/local/libexec/wgdashbackup_progress.py
restore_milestone() {
  local phase=$1 percent=$2
  [[ -n "$RESTORE_JOB_ID" ]] || return 0
  python3 "$RESTORE_PROGRESS_SCRIPT" "$RESTORE_JOB_ID" running "$percent" "$phase" ||
    log "WARN: cannot update restore progress ($phase)"
}
restore_approved_cleanup() {
  local rc=$1
  if [[ -n "$RESTORE_JOB_ID" ]]; then
    if (( rc == 0 )); then
      python3 "$RESTORE_PROGRESS_SCRIPT" "$RESTORE_JOB_ID" completed 100 completed ||
        log 'WARN: unable to publish completed restore'
    else
      python3 "$RESTORE_PROGRESS_SCRIPT" "$RESTORE_JOB_ID" failed 0 failed ||
        log 'WARN: unable to publish restore failure'
    fi
  fi
  if (( watchdog_active )); then systemctl start wgd-watchdog.service || :; fi
}

log() {
  local line
  line="$(date -u '+%Y-%m-%dT%H:%M:%SZ') $*"
  printf '%s\n' "$line"
  if [[ -d /var/log ]]; then printf '%s\n' "$line" >> "$LOG"; fi
}
fail() { log "ERROR: $*"; return 1; }
need_root() { (( EUID == 0 )) || { echo 'Run with sudo: sudo wgdashbackup' >&2; exit 1; }; }
ensure_dirs() { install -d -m 700 "$CFG_DIR" "$OUT"; touch "$LOG"; chmod 600 "$LOG"; }
need_install() {
  for tool in docker python3 tar gzip curl flock sha256sum split systemctl; do
    command -v "$tool" >/dev/null || { echo "Missing: $tool" >&2; return 1; }
  done
  docker compose version >/dev/null || { echo 'Docker Compose plugin is missing.' >&2; return 1; }
  [[ -f "$COMPOSE" ]] || { echo "Compose not found: $COMPOSE" >&2; return 1; }
  docker inspect "$CONTAINER" >/dev/null || { echo "Container not found: $CONTAINER" >&2; return 1; }
}
load_telegram() {
  [[ -s "$CFG" ]] || { fail 'Configure the Telegram bot from menu option 1 first.'; return 1; }
  # Config is root-owned and written with bash %q quoting by this script.
  # shellcheck source=/dev/null
  source "$CFG"
  [[ -n "${TOKEN:-}" && -n "${CHAT:-}" ]] || fail 'Telegram settings are incomplete.'
}
interval_minutes() {
  local minutes=30
  [[ ! -f "$SCHEDULE" ]] || read -r minutes < "$SCHEDULE" || :
  [[ "$minutes" =~ ^[0-9]+$ ]] && (( minutes >= 5 && minutes <= 1440 )) || {
    echo 'Invalid saved interval; using 30 minutes.' >&2
    minutes=30
  }
  printf '%s' "$minutes"
}
set_interval() {
  local minutes
  read -r -p 'Backup interval in minutes (5-1440; e.g. 15/30/60): ' minutes </dev/tty
  [[ "$minutes" =~ ^[0-9]+$ ]] && (( minutes >= 5 && minutes <= 1440 )) || {
    echo 'Invalid interval; nothing changed.' >&2; return 1;
  }
  printf '%s\n' "$minutes" > "$SCHEDULE"
  chmod 600 "$SCHEDULE"
  install_timer
  if [[ -s "$CFG" ]]; then
    systemctl enable --now "$UNIT.timer" "$HEALTH_UNIT.timer" >/dev/null
    systemctl restart "$UNIT.timer"
  fi
  log "Backup interval set to $minutes minutes (after previous run finishes; boot trigger also enabled)."
  systemctl list-timers "$UNIT.timer" --no-pager || :
}
now_epoch() { date +%s; }
file_epoch() { local value=0; [[ -f "$1" ]] && read -r value < "$1" || :; [[ "$value" =~ ^[0-9]+$ ]] || value=0; printf '%s' "$value"; }
telegram_call() {
  # Never expose bot token in process arguments or logs.
  local method=$1 response=$2; shift 2
  curl -fsS --retry 2 --retry-delay 2 --connect-timeout 20 --max-time 600 \
    -o "$response" "$@" --config - <<EOF
url = "https://api.telegram.org/bot${TOKEN}/${method}"
EOF
  python3 - "$response" <<'PY'
import json,sys
with open(sys.argv[1], encoding='utf-8') as f:
    response=json.load(f)
if response.get('ok') is not True:
    raise SystemExit('Telegram API error: ' + str(response.get('description', 'Unknown error')))
PY
}
install_timer() {
  local minutes
  minutes=$(interval_minutes)
  cat > "/etc/systemd/system/$UNIT.service" <<EOF
[Unit]
Description=Live WGDashboard SQLite backup and Telegram delivery
After=docker.service
Requires=docker.service
[Service]
Type=oneshot
ExecStart=$BIN --backup
TimeoutStartSec=0
Nice=15
IOSchedulingClass=idle
EOF
  cat > "/etc/systemd/system/$UNIT.timer" <<EOF
[Unit]
Description=WGDashboard online backup every $minutes minutes
[Timer]
OnBootSec=2min
OnUnitInactiveSec=${minutes}min
AccuracySec=1min
Unit=$UNIT.service
[Install]
WantedBy=timers.target
EOF
  cat > "/etc/systemd/system/$HEALTH_UNIT.service" <<EOF
[Unit]
Description=WGDashboard backup health check, retries and Telegram alert
After=docker.service
Requires=docker.service
[Service]
Type=oneshot
ExecStart=$BIN --health
TimeoutStartSec=90
Nice=19
IOSchedulingClass=idle
EOF
  cat > "/etc/systemd/system/$HEALTH_UNIT.timer" <<EOF
[Unit]
Description=Check WGDashboard backup health every 5 minutes
[Timer]
OnCalendar=*-*-* *:0/5:00
Persistent=true
Unit=$HEALTH_UNIT.service
[Install]
WantedBy=timers.target
EOF
  chmod 644 "/etc/systemd/system/$UNIT.service" "/etc/systemd/system/$UNIT.timer"     "/etc/systemd/system/$HEALTH_UNIT.service" "/etc/systemd/system/$HEALTH_UNIT.timer"
  systemd-analyze verify "/etc/systemd/system/$UNIT.service" "/etc/systemd/system/$HEALTH_UNIT.service"
  systemctl daemon-reload
  # Legacy script used this name; disable its schedule only, NEVER touch VPN.
  if systemctl list-unit-files wgd-backup.timer --no-legend 2>/dev/null | grep -q '^wgd-backup.timer'; then
    systemctl disable --now wgd-backup.timer >/dev/null 2>&1 || true
    log 'Disabled legacy wgd-backup.timer (only backup schedule).'
  fi
}
configure() {
  local token chat temp
  echo 'Telegram bot: send /start to the bot first. Do NOT paste credentials in chat.'
  read -r -s -p 'Bot token (hidden): ' token </dev/tty; echo
  read -r -p 'Chat ID / @channel: ' chat </dev/tty
  [[ "$token" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]] || { fail 'Invalid token format.'; return 1; }
  [[ "$chat" =~ ^-?[0-9]+$ || "$chat" =~ ^@[A-Za-z0-9_]+$ ]] || { fail 'Invalid Chat ID.'; return 1; }
  TOKEN=$token; CHAT=$chat
  temp=$(mktemp "$CFG_DIR/.telegram.XXXXXX")
  chmod 600 "$temp"
  if ! telegram_call getMe "$temp.response"; then rm -f "$temp" "$temp.response"; return 1; fi
  rm -f "$temp.response"
  if ! telegram_call sendMessage "$temp.response" -F "chat_id=$chat" -F 'text=WGDashboard backup: Telegram destination test OK'; then
    rm -f "$temp" "$temp.response"; fail 'Bot cannot send to this Chat ID (start the bot / add it to the channel).'; return 1
  fi
  rm -f "$temp.response"
  printf 'TOKEN=%q\nCHAT=%q\n' "$token" "$chat" > "$temp"
  mv -f "$temp" "$CFG"
  chmod 600 "$CFG"
  unset token chat TOKEN CHAT
  install_timer
  systemctl enable --now "$UNIT.timer" "$HEALTH_UNIT.timer" >/dev/null
  log "Telegram credentials saved; $(interval_minutes)-minute schedule enabled."
  echo 'Starting the first backup now...'
  "$BIN" --backup
}

# Restricted, non-interactive entrypoints for the host-only panel helper.
configure_stdin() {
  local token chat temp response
  IFS= read -r token || { fail 'Missing Telegram token.'; return 1; }
  IFS= read -r chat || { fail 'Missing Chat ID.'; return 1; }
  [[ "$token" =~ ^[0-9]+:[A-Za-z0-9_-]+$ && ${#token} -le 200 ]] ||
    { fail 'Invalid token format.'; return 1; }
  [[ "$chat" =~ ^-?[0-9]+$ || "$chat" =~ ^@[A-Za-z0-9_]+$ ]] ||
    { fail 'Invalid Chat ID.'; return 1; }
  TOKEN=$token CHAT=$chat
  temp=$(mktemp "$CFG_DIR/.telegram.XXXXXXXX")
  response=$(mktemp "$CFG_DIR/.telegram-test.XXXXXXXX")
  chmod 600 "$temp" "$response"
  if ! telegram_call getMe "$response" ||
     ! telegram_call sendMessage "$response" -F "chat_id=$CHAT" \
       -F 'text=WireDash: Telegram backup destination verified'; then
    rm -f -- "$temp" "$response"
    unset TOKEN CHAT token chat
    fail 'Telegram verification failed; previous settings preserved.'
    return 1
  fi
  rm -f -- "$response"
  printf 'TOKEN=%q\nCHAT=%q\n' "$token" "$chat" > "$temp"
  mv -f -- "$temp" "$CFG"
  chmod 600 "$CFG"
  unset TOKEN CHAT token chat
  install_timer
  systemctl enable --now "$UNIT.timer" "$HEALTH_UNIT.timer" >/dev/null
  log 'Telegram backup destination updated and schedule enabled.'
  systemctl start --no-block "$UNIT.service"
}
set_interval_noninteractive() {
  local minutes=$1
  [[ "$minutes" =~ ^[0-9]+$ ]] && (( minutes >= 5 && minutes <= 1440 )) ||
    { fail 'Backup interval must be 5..1440 minutes.'; return 1; }
  printf '%s\n' "$minutes" > "$SCHEDULE"
  chmod 600 "$SCHEDULE"
  install_timer
  if systemctl is-enabled --quiet "$UNIT.timer"; then
    systemctl restart "$UNIT.timer"
  fi
  log "Backup schedule set to $minutes minutes."
}
enable_schedule() {
  [[ -s "$CFG" ]] || { fail 'Configure Telegram first.'; return 1; }
  install_timer
  systemctl enable --now "$UNIT.timer" "$HEALTH_UNIT.timer" >/dev/null
  log 'Scheduled backups enabled.'
}
disable_schedule() {
  systemctl disable --now "$UNIT.timer" "$HEALTH_UNIT.timer" >/dev/null
  log 'Scheduled backups disabled; VPN untouched.'
}

mount_sources() {
  local -a paths
  mapfile -t paths < <(docker inspect "$CONTAINER" | python3 -c '
import json,sys
j=json.load(sys.stdin)[0]
d={m["Destination"]:m["Source"] for m in j["Mounts"]}
for p in ("/data","/etc/wireguard","/etc/amnezia/amneziawg"):
 print(d.get(p,""))')
  (( ${#paths[@]} == 3 )) || fail 'Unable to read Docker mounts.'
  DATA=${paths[0]}; WG=${paths[1]}; AWG=${paths[2]}
  for p in "$DATA" "$WG" "$AWG" "$DATA/db"; do
    [[ -n "$p" && -d "$p" ]] || { fail "Missing mount/directory: $p"; return 1; }
  done
  [[ -f "$DATA/wg-dashboard.ini" ]] || fail 'Panel config missing from /data.'
}
fingerprint() {
  {
    find "$DATA" -path "$DATA/db" -prune -o -type f -print0
    find "$WG" "$AWG" -type f -print0
    printf '%s\0' "$COMPOSE"
    [[ ! -f /opt/wgdashboard/.env ]] || printf '%s\0' /opt/wgdashboard/.env
  } | sort -z | xargs -0 -r sha256sum | sha256sum | cut -d' ' -f1
}
online_sqlite() {
  python3 - "$DATA" "$1" <<'PY'
import configparser,sqlite3,sys,shutil
from pathlib import Path
from urllib.parse import quote
from contextlib import closing
source=Path(sys.argv[1]); out=Path(sys.argv[2]); inp=source/'db'
c=configparser.RawConfigParser(strict=False)
c.read(source/'wg-dashboard.ini')
if c.get('Database','type',fallback='sqlite').lower() != 'sqlite':
    raise SystemExit('External database detected: SQLite-only backup; refusing incomplete archive.')
dbs=sorted(p for p in inp.rglob('*') if p.is_file() and p.suffix.lower() in ('.db','.sqlite','.sqlite3'))
if not dbs: raise SystemExit('No SQLite databases in /data/db.')
for p in inp.rglob('*'):
    if not p.is_file() or p in dbs or p.name.endswith(('-wal','-shm','-journal')): continue
    dest=out/p.relative_to(inp); dest.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,dest)
for p in dbs:
    dest=out/p.relative_to(inp); dest.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect('file:'+quote(str(p),safe='/')+'?mode=ro',uri=True,timeout=30)) as src:
        with closing(sqlite3.connect(dest,timeout=30)) as dst:
            src.backup(dst,pages=256,sleep=.1)
            if dst.execute('PRAGMA quick_check').fetchone()!=('ok',):
                raise SystemExit('SQLite integrity check failed: '+str(p))
            # Only compact the OFFLINE BACKUP SNAPSHOT, never the active DB.
            # VACUUM removes freelist pages that have accumulated as peers
            # are added/deleted. Skip small or non-bloated DBs to save CPU.
            pages=dst.execute('PRAGMA page_count').fetchone()[0]
            free_pages=dst.execute('PRAGMA freelist_count').fetchone()[0]
            page_size=dst.execute('PRAGMA page_size').fetchone()[0]
            if (pages and free_pages*page_size >= 16*1024*1024 and
                    free_pages*10 >= pages and
                    shutil.disk_usage(dest.parent).free >=
                    dest.stat().st_size*2 + 1024*1024*1024):
                before=dest.stat().st_size
                dst.execute('VACUUM')
                if dst.execute('PRAGMA quick_check').fetchone()!=('ok',):
                    raise SystemExit('Compacted SQLite snapshot failed integrity check: '+str(p))
                print('Backup-only SQLite compaction:',p.name,before,'->',dest.stat().st_size)
print('SQLite snapshots checked:',len(dbs))
PY
}
backup() {
  local tmp stage before after name archive sha response part i total size
  local -a parts=() old=()
  ensure_dirs; need_install; load_telegram
  exec 9>/run/wgdashbackup.lock
  if ! flock -n 9; then log 'A backup is already in progress; no concurrent run.'; return 0; fi
  now_epoch > "$LAST_ATTEMPT"
  [[ $(docker inspect -f '{{.State.Running}}' "$CONTAINER") == true ]] || fail 'WGDashboard container is not running.'
  mount_sources
  tmp=$(mktemp -d "$OUT/.working.XXXXXXXX")
  trap 'rm -rf -- "$tmp"' EXIT
  stage=$tmp/snapshot
  mkdir -p "$stage/data/db" "$stage/etc/wireguard" "$stage/etc/amnezia/amneziawg"
  log 'Starting ONLINE backup (no Docker stop/restart).'
  before=$(fingerprint)
  tar -C "$DATA" --exclude='./db' -cf - . | tar -C "$stage/data" -xf -
  cp -a "$WG/." "$stage/etc/wireguard/"
  cp -a "$AWG/." "$stage/etc/amnezia/amneziawg/"
  cp -L --preserve=mode,ownership,timestamps "$COMPOSE" "$stage/compose.yaml"
  [[ ! -f /opt/wgdashboard/.env ]] || cp -L --preserve=mode,ownership,timestamps /opt/wgdashboard/.env "$stage/.env"
  online_sqlite "$stage/data/db"
  after=$(fingerprint)
  [[ "$before" == "$after" ]] || fail 'Configuration changed during capture. No backup sent; retry manually or on the next timer.'
  printf 'UTC=%s\nImage=%s\n' "$(date -u +%FT%TZ)" "$(docker inspect -f '{{.Config.Image}}' "$CONTAINER")" > "$stage/manifest.txt"
  name="wgdashboard-$(date -u +%Y%m%d-%H%M%S).tar.gz"
  archive=$tmp/$name
  # Maximum portable gzip level; archive name and restore format unchanged.
  # This may require more CPU but never rewrites the live SQLite database.
  tar -C "$stage" -cf - data etc compose.yaml manifest.txt $( [[ ! -e "$stage/.env" ]] || printf '%s' '.env' ) | gzip -9 > "$archive"
  tar -tzf "$archive" >/dev/null
  sha=$(sha256sum "$archive" | cut -d' ' -f1)
  mv "$archive" "$OUT/$name"
  archive=$OUT/$name
  printf '%s  %s\n' "$sha" "$name" > "$OUT/$name.sha256"
  # Keep the newest four undelivered local snapshots. Otherwise Telegram outages
  # could fill the host disk even though sent-backup retention is bounded.
  mapfile -t old < <(find "$OUT" -maxdepth 1 -type f -name 'wgdashboard-*.tar.gz' \
    -printf '%T@ %p\\n' | sort -nr | cut -d' ' -f2- | \
    while IFS= read -r pending; do
      [[ -f "$pending.sent" || -f "$pending.imported" ]] || printf '%s\\n' "$pending"
    done | tail -n +5)
  for part in "${old[@]}"; do rm -f -- "$part" "$part.sha256"; done

  size=$(stat -c%s "$archive")
  log "Compressed backup size: $size bytes (gzip -9, compatible tar.gz)"
  if (( size > 45000000 )); then
    split -b 45000000 -d -a 4 "$archive" "$tmp/$name.part-"
    shopt -s nullglob
    parts=("$tmp/$name.part-"*)
    shopt -u nullglob
  else
    parts=("$archive")
  fi
  total=${#parts[@]}; i=0
  log "Archive: $name; size: $size bytes; SHA256: $sha; Telegram parts: $total"
  for part in "${parts[@]}"; do
    i=$((i+1)); response=$tmp/telegram.json
    telegram_call sendDocument "$response" \
      -F "chat_id=$CHAT" -F "document=@$part" \
      -F "caption=WGDashboard $name | PART $i/$total | SHA256 $sha"
    log "Telegram upload accepted: part $i/$total"
  done
  touch "$OUT/$name.sent"
  chmod 600 "$OUT/$name.sent"
  printf '%s | %s | %s | %s parts\n' "$(date -u +%FT%TZ)" "$name" "$sha" "$total" > "$CFG_DIR/last-success"
  # Record only AFTER ALL Telegram parts have been accepted.
  now_epoch > "$LAST_OK"
  rm -f "$LAST_FAIL" "$RETRY_STATE"
  chmod 600 "$CFG_DIR/last-success" "$LAST_OK"
  # Retain four fully uploaded archives. Preserve any older backups that failed to upload.
  mapfile -t old < <(find "$OUT" -maxdepth 1 -type f -name 'wgdashboard-*.tar.gz.sent' -printf '%T@ %p\n' | sort -nr | tail -n +5 | cut -d' ' -f2-)
  for part in "${old[@]}"; do
    rm -f -- "$part" "${part%.sent}" "${part%.sent}.sha256"
  done
  log "SUCCESS: Telegram received all $total part(s) of $name"
  trap - EXIT; rm -rf -- "$tmp"
}
# Validate archive paths and extraction safety before unpacking / restoring.
validate_archive() {
  python3 - "$1" <<'PY'
import sys,tarfile,posixpath
p=sys.argv[1]; names=set()
with tarfile.open(p,'r:gz') as t:
    for m in t:
        if any(component=='..' for component in m.name.split('/')):
            raise SystemExit('Path traversal in TAR entry: '+m.name)
        n=posixpath.normpath(m.name)
        if n.startswith('/') or n=='..' or n.startswith('../') or not (m.isdir() or m.isfile()):
            raise SystemExit('Unsafe or unsupported TAR entry: '+m.name)
        if not (n in ('.','data','etc','etc/wireguard','etc/amnezia','etc/amnezia/amneziawg','compose.yaml','.env','manifest.txt') or
                n.startswith(('data/','etc/wireguard/','etc/amnezia/amneziawg/'))):
            raise SystemExit('Unexpected TAR entry: '+m.name)
        names.add(n)
for required in ('data/wg-dashboard.ini','compose.yaml'):
    if required not in names: raise SystemExit('Missing required entry: '+required)
if not any(n.startswith('data/db/') and n.endswith(('.db','.sqlite','.sqlite3')) for n in names):
    raise SystemExit('Missing SQLite database files.')
print('Archive structure: OK; entries:',len(names))
PY
}
# Accept a full .tar.gz, or the first of consecutively named .part-000 / .part-0000 files.
# Paths pasted with one matching pair of single/double quotes are normalized automatically.
resolve_archive() {
  local f=$1 tmp=$2 base n path width expected=0

  # Be forgiving when a path was copied/pasted with surrounding quotes.
  if (( ${#f} >= 2 )); then
    if [[ "$f" == \"*\" || "$f" == \'*\' ]]; then
      f=${f:1:${#f}-2}
    fi
  fi

  [[ -f "$f" ]] || fail "Backup not found: $f"

  if [[ "$f" =~ ^(.*)\.part-([0-9]{3}|[0-9]{4})$ ]]; then
    base=${BASH_REMATCH[1]}
    width=${#BASH_REMATCH[2]}
    shopt -s nullglob
    local -a parts=()
    if (( width == 3 )); then
      parts=("$base".part-[0-9][0-9][0-9])
    else
      parts=("$base".part-[0-9][0-9][0-9][0-9])
    fi
    shopt -u nullglob
    (( ${#parts[@]} )) || fail 'No backup parts.'

    for path in "${parts[@]}"; do
      printf -v n "%0${width}d" "$expected"
      [[ "$path" == "$base.part-$n" ]] || fail "Missing/out-of-order part: $n"
      expected=$((expected+1))
    done

    cat -- "${parts[@]}" > "$tmp/rejoined.tar.gz"
    RESOLVED=$tmp/rejoined.tar.gz
  else
    RESOLVED=$f
  fi
}

verify_backup() {
  local original=$1 expected=${2:-} tmp db count=0
  tmp=$(mktemp -d "$OUT/.verify.XXXXXXXX")
  resolve_archive "$original" "$tmp"
  if [[ -z "$expected" && -f "$RESOLVED.sha256" ]]; then
    expected=$(cut -d' ' -f1 < "$RESOLVED.sha256")
  fi
  if [[ -n "$expected" ]]; then
    [[ "$expected" =~ ^[a-fA-F0-9]{64}$ ]] || fail 'Invalid SHA256 format.'
    [[ $(sha256sum "$RESOLVED" | cut -d' ' -f1) == "$expected" ]] || fail 'SHA256 mismatch.'
    log 'SHA256: OK'
  else
    log 'SHA256 not provided; only TAR/SQLite integrity will be checked.'
  fi
  validate_archive "$RESOLVED"
  mkdir -p "$tmp/check"
  tar -xzf "$RESOLVED" -C "$tmp/check"
  python3 - "$tmp/check/data/db" <<'PY'
import sqlite3,sys
from pathlib import Path
from contextlib import closing
p=Path(sys.argv[1]); dbs=[f for f in p.rglob('*') if f.is_file() and f.suffix.lower() in ('.db','.sqlite','.sqlite3')]
if not dbs: raise SystemExit('No databases to verify.')
for db in dbs:
    with closing(sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True)) as con:
        if con.execute('PRAGMA quick_check').fetchone()!=('ok',): raise SystemExit('SQLite check failed: '+str(db))
print('Database integrity: OK;',len(dbs),'database(s)')
PY
  log "VERIFIED: $(basename "$original")"
  rm -rf -- "$tmp"
}
restore() {
  local f=$1 expected=${2:-} mode=${3:-interactive} tmp pre reply
  exec 7>/run/wgdashbackup-restore.lock
  flock -n 7 || { fail 'A full restore is already in progress.'; return 1; }
  restore_milestone preparing 5
  need_install; ensure_dirs; mount_sources
  tmp=$(mktemp -d "$OUT/.restore.XXXXXXXX")
  resolve_archive "$f" "$tmp"
  # Validate BEFORE stopping anything. These are milestone percentages,
  # NOT progress inferred from wall-clock duration or the compressed size.
  restore_milestone verifying 10
  verify_backup "$RESOLVED" "$expected"
  restore_milestone extracting 30
  mkdir -p "$tmp/unpack"
  tar -xzf "$RESOLVED" -C "$tmp/unpack"
  echo 'WARNING: Restore STOPS WGDashboard/VPN and REPLACES current volume data.'
  echo 'Before this step, install the same Docker Compose deployment on the new VPS and copy the backup to /root.'
  echo 'This is ONLY for recovery, never for regular backup.'
  if [[ "$mode" != approved ]]; then
    read -r -p 'Type RESTORE to continue: ' reply </dev/tty
    if [[ "$reply" != RESTORE ]]; then rm -rf -- "$tmp"; log 'Restore cancelled.'; return 0; fi
  fi
  restore_milestone stopping 45
  cd /opt/wgdashboard
  docker compose -f "$COMPOSE" stop wgdashboard
  restore_milestone snapshotting 55
  mkdir -p "$tmp/previous/data" "$tmp/previous/etc/wireguard" "$tmp/previous/etc/amnezia/amneziawg"
  cp -a "$DATA/." "$tmp/previous/data/"
  cp -a "$WG/." "$tmp/previous/etc/wireguard/"
  cp -a "$AWG/." "$tmp/previous/etc/amnezia/amneziawg/"
  cp -L --preserve=mode,ownership,timestamps "$COMPOSE" "$tmp/previous/compose.yaml"
  [[ ! -e /opt/wgdashboard/.env ]] || cp -L --preserve=mode,ownership,timestamps /opt/wgdashboard/.env "$tmp/previous/.env"
  pre="$OUT/pre-restore-$(date -u +%Y%m%d-%H%M%S).tar.gz"
  tar -C "$tmp/previous" -czf "$pre" .
  tar -tzf "$pre" >/dev/null
  log "Previous state preserved locally: $pre"
  restore_milestone applying 75
  find "$DATA" "$WG" "$AWG" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
  cp -a "$tmp/unpack/data/." "$DATA/"
  cp -a "$tmp/unpack/etc/wireguard/." "$WG/"
  cp -a "$tmp/unpack/etc/amnezia/amneziawg/." "$AWG/"
  if [[ "$mode" == approved ]]; then
    # Preserve the managed WireDash deployment (HTTP port, image pin,
    # host backup socket and watchdog integration). The backup restores all
    # persistent VPN/users/databases, but must not uninstall the admin panel
    # and its progress API while it is recovering.
    log 'Managed restore: retained current Compose and .env deployment settings.'
  else
    cp -a "$tmp/unpack/compose.yaml" "$COMPOSE"
    [[ ! -e "$tmp/unpack/.env" ]] || cp -a "$tmp/unpack/.env" /opt/wgdashboard/.env
  fi
  restore_milestone starting 90
  docker compose -f "$COMPOSE" up -d wgdashboard
  restore_milestone checking 95
  local health='' i
  for i in {1..40}; do
    health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER") || :
    [[ "$health" != healthy && "$health" != running ]] || break
    sleep 3
  done
  [[ "$health" == healthy || "$health" == running ]] ||
    fail "Restored container did not become healthy (last: $health)"
  log 'Restore completed; container running. Verify VPN clients and endpoints.'
  rm -rf -- "$tmp"
}
# Send a compact sanitized diagnostic. If Telegram itself fails, retain details locally;
# otherwise messages have a 30-minute anti-spam cooldown.
alert() {
  local reason=$1 now last tmp text
  [[ -s "$CFG" ]] || { log 'Alert unavailable: configure Telegram.'; return 0; }
  now=$(now_epoch); last=$(file_epoch "$LAST_ALERT")
  if (( now-last < 1800 )); then log 'Alert suppressed (30-minute cooldown).'; return 0; fi
  load_telegram || return 0
  tmp=$(mktemp "$OUT/.alert.XXXXXXXX")
  text="WGDashboard BACKUP ALERT: $reason"$'\n'"Host: $(hostname)"$'\n'"UTC: $(date -u +%FT%TZ)"$'\n'"Last log lines:"$'\n'"$(tail -n 12 "$LOG" | cut -c1-180)"
  if telegram_call sendMessage "$tmp" -F "chat_id=$CHAT" --form-string "text=${text:0:3800}"; then
    now_epoch > "$LAST_ALERT"; log 'Failure/health alert delivered to Telegram.'
  else
    log 'Could not send Telegram alert; see local journal/log (possibly Telegram unavailable).'
  fi
  rm -f -- "$tmp"
  return 0
}
backup_failed() {
  local rc=$1
  trap - ERR
  now_epoch > "$LAST_FAIL"
  log "BACKUP FAILED (exit code $rc). No VPN or Docker restart attempted."
  alert "Backup job failed (exit $rc); retry checker is enabled." || :
  exit "$rc"
}
health() {
  ensure_dirs
  exec 8>/run/wgdashbackup-health.lock
  flock -n 8 || return 0
  # If user deliberately disabled backups, do not alert/restart them.
  systemctl is-enabled --quiet "$UNIT.timer" || { log 'Health: backup timer disabled, no action.'; return 0; }
  local now lastok failed attempted age active slot current=0 count=0 lasttry=0 stale
  now=$(now_epoch); lastok=$(file_epoch "$LAST_OK")
  failed=$(file_epoch "$LAST_FAIL"); attempted=$(file_epoch "$LAST_ATTEMPT")
  age=$((now-lastok))
  stale=$(( ($(interval_minutes)+15)*60 ))
  active=$(systemctl show "$UNIT.service" -p ActiveState --value 2>/dev/null || echo unknown)
  if [[ "$active" == activating || "$active" == active || "$active" == reloading ]]; then
    log 'Health: backup service is busy; no duplicate started.'; return 0
  fi
  # A recent failure or a stale delivery is a health violation; adjust to the interval.
  if (( failed <= lastok && age < stale )); then
    log "Health OK: last Telegram delivery ${age}s ago."
    return 0
  fi
  if (( age >= stale )); then alert "No successful Telegram backup for ${age}s (threshold ${stale}s)."; fi
  if (( now-attempted < 300 )); then log 'Health: waiting 5 minutes between attempts.'; return 0; fi
  # Never exceed three health-triggered retries per 30-minute time window.
  slot=$((now/1800))
  if [[ -f "$RETRY_STATE" ]]; then
    read -r current count lasttry < "$RETRY_STATE" || :
    [[ "$current" =~ ^[0-9]+$ && "$count" =~ ^[0-9]+$ ]] || { current=0; count=0; }
  fi
  [[ "$current" == "$slot" ]] || count=0
  if (( count >= 3 )); then
    log 'Health: retry limit 3/30m reached; normal timer will try again.'
    return 0
  fi
  count=$((count+1))
  printf '%s %s %s\n' "$slot" "$count" "$now" > "$RETRY_STATE"
  log "Health: starting retry $count/3 without touching Docker."
  if ! systemctl reset-failed "$UNIT.service" || ! systemctl start --no-block "$UNIT.service"; then
    log 'Health: could not queue retry; will try on next check.'
    alert 'Could not queue automatic backup retry.'
    return 1
  fi
}
status() {
  local ok now age
  now=$(now_epoch); ok=$(file_epoch "$LAST_OK")
  echo "--- Backup interval: $(interval_minutes) minutes after each completed job; also 2min after boot ---"
  echo '--- Backup & health timers ---'
  systemctl list-timers "$UNIT.timer" "$HEALTH_UNIT.timer" --no-pager || true
  echo '--- Last backup service run ---'
  systemctl show "$UNIT.service" -p Result -p ExecMainStatus --no-pager || true
  echo '--- Container (read-only status) ---'
  docker ps --filter "name=^/${CONTAINER}$" --format '{{.Names}} {{.Status}}' || true
  echo '--- Last confirmed Telegram delivery ---'
  if [[ -f "$CFG_DIR/last-success" ]]; then
    cat "$CFG_DIR/last-success"; echo "Age: $((now-ok)) seconds"
  else echo 'None recorded'; fi
  echo '--- Last failure ---'
  [[ ! -f "$LAST_FAIL" ]] || date -u -d "@$(file_epoch "$LAST_FAIL")" || :
  echo '--- Local backups ---'; du -sh "$OUT" 2>/dev/null || true
}
list_backups() {
  find "$OUT" -maxdepth 1 -type f \( -name 'wgdashboard-*.tar.gz' -o -name 'pre-restore-*.tar.gz' \) \
    -printf '%TY-%Tm-%Td %TH:%TM | %k KB | %p\n' | sort -r | head -n 30
}
menu() {
  local choice file sha
  while true; do
    echo
    echo '====== WGDashboard Backup Manager (wireback) ======'
    echo '1) Set/change Telegram token + Chat ID (then send first backup)'
    echo '2) Backup and send NOW (VPN stays running)'
    echo '3) Health / status / last Telegram success'
    echo '4) List local backups'
    echo '5) Verify archive / downloaded Telegram parts'
    echo '6) RESTORE (recovery only; stops VPN)'
    echo '7) Show last 50 log lines'
    echo '10) Run health check now'
    echo '11) Change backup interval (minutes)'
    echo "8) Enable scheduled backups (current: $(interval_minutes) min)"
    echo '9) Disable schedule (manual backup still works)'
    echo '0) Exit'
    read -r -p 'Choose: ' choice </dev/tty
    case "$choice" in
      1) configure || echo 'Setup failed; see log.' ;;
      2) "$BIN" --backup || echo 'Backup failed; see log.' ;;
      3) status ;;
      4) list_backups ;;
      5) read -r -p 'Archive or first part path: ' file </dev/tty
         read -r -p 'Optional SHA256 from Telegram caption (Enter to skip): ' sha </dev/tty
         "$BIN" --verify "$file" "$sha" || echo 'Verification failed.' ;;
      6) read -r -p 'Archive or first part path: ' file </dev/tty
         read -r -p 'Optional SHA256 from Telegram caption (Enter to skip): ' sha </dev/tty
         "$BIN" --restore "$file" "$sha" || echo 'Restore FAILED; inspect log. If you already typed RESTORE, check container/VPN state.' ;;
      7) tail -n 50 "$LOG" 2>/dev/null || true
         journalctl -u "$UNIT.service" -n 20 --no-pager || true ;;
      8) [[ -s "$CFG" ]] || { echo 'Configure Telegram first (option 1).'; continue; }
         install_timer; systemctl enable --now "$UNIT.timer" "$HEALTH_UNIT.timer"; status ;;
      9) systemctl disable --now "$UNIT.timer" "$HEALTH_UNIT.timer"; echo 'Only backup/health timers stopped; VPN is untouched.' ;;
      10) "$BIN" --health; status ;;
      11) set_interval || echo "Schedule change failed." ;;
      0) return 0 ;;
      *) echo 'Invalid selection.' ;;
    esac
  done
}
main() {
  need_root; ensure_dirs
  case "${1:-}" in
    --install)
      need_install
      if [[ "$(readlink -f "$0")" != "$BIN" ]]; then
        install -m 700 "$0" "$BIN.new"
        mv -f "$BIN.new" "$BIN"
      fi
      if [[ "$(readlink -f /usr/local/bin/wireback 2>/dev/null || :)" != "$BIN" ]]; then
        ln -sfn "$BIN" /usr/local/bin/wireback
      fi
      if [[ ! -f /etc/systemd/system/$UNIT.timer || ! -f /etc/systemd/system/$HEALTH_UNIT.timer ]]; then
        install_timer
      else
        echo 'Backup units already installed.'
      fi
      if [[ -s "$CFG" ]]; then
        systemctl enable --now "$UNIT.timer" "$HEALTH_UNIT.timer"
      else
        echo 'Telegram not configured; backup timers remain disabled.'
      fi
      echo 'Ready. Run: sudo wireback'
      ;;
    --backup) trap 'backup_failed "$?"' ERR; backup; trap - ERR ;;
    --configure-stdin) configure_stdin ;;
    --set-interval) [[ -n "${2:-}" ]] || { echo 'Missing interval' >&2; exit 2; }; set_interval_noninteractive "$2" ;;
    --enable) enable_schedule ;;
    --disable) disable_schedule ;;
    --telegram-login)
      need_root
      [[ -f /usr/local/libexec/wgdashbackup_telegram.py ]] ||
        { echo 'Telegram downloader missing. Upgrade WireDash first.' >&2; exit 1; }
      if [[ ! -x /opt/wgdashbackup-telegram/venv/bin/python3 ]]; then
        command -v python3 >/dev/null && python3 -m venv /opt/wgdashbackup-telegram/venv ||
          { echo 'Install python3-venv: apt install python3-venv' >&2; exit 1; }
      fi
      if ! /opt/wgdashbackup-telegram/venv/bin/python3 -c 'import telethon' >/dev/null 2>&1; then
        /opt/wgdashbackup-telegram/venv/bin/python3 -m pip install \
          --disable-pip-version-check 'telethon==1.45.0' ||
          { echo 'Could not install Telegram client dependency.' >&2; exit 1; }
      fi
      /opt/wgdashbackup-telegram/venv/bin/python3 \
        /usr/local/libexec/wgdashbackup_telegram.py login
      ;;
    --health) health ;;
    --verify) [[ -n "${2:-}" ]] || { echo 'Usage: wgdashbackup --verify FILE [SHA256]'; exit 2; }; verify_backup "$2" "${3:-}" ;;
    --restore) [[ -n "${2:-}" ]] || { echo 'Usage: wgdashbackup --restore FILE [SHA256]'; exit 2; }; restore "$2" "${3:-}" ;;
    --restore-approved)
      [[ -n "${2:-}" ]] || { echo 'Missing approved archive' >&2; exit 2; }
      RESTORE_JOB_ID=${4:-}
      [[ -z "$RESTORE_JOB_ID" || "$RESTORE_JOB_ID" =~ ^[a-f0-9]{12}$ ]] ||
        { echo 'Invalid progress job token' >&2; exit 2; }
      watchdog_active=0
      trap 'restore_approved_cleanup "$?"' EXIT
      if systemctl is-active --quiet wgd-watchdog.service; then
        watchdog_active=1
        systemctl stop wgd-watchdog.service
      fi
      restore "$2" "${3:-}" approved
      ;;
    --status) status ;;
    --help|-h) echo 'Usage: sudo wireback [--install | --backup | --health | --verify FILE [SHA256] | --restore FILE [SHA256] | --status] (also: wgdashbackup)' ;;
    '')
      need_install
      if [[ "$(readlink -f "$0")" != "$BIN" ]]; then
        install -m 700 "$0" "$BIN.new"
        mv -f "$BIN.new" "$BIN"
        log "Installed command: $BIN"
      fi
      ln -sfn "$BIN" /usr/local/bin/wireback
      if [[ -s "$CFG" ]]; then
        install_timer
        if systemctl is-enabled --quiet "$UNIT.timer"; then
          systemctl enable --now "$HEALTH_UNIT.timer" >/dev/null
        fi
      fi
      echo 'Installed. Run again anytime: sudo wgdashbackup'
      menu ;;
    *) echo 'Unknown option; use --help.' >&2; exit 2 ;;
  esac
}
main "$@"