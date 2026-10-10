#!/usr/bin/env bash
# The stable URL on main is a bootstrap. Every actual install/update resolves
# exactly one immutable main commit and uses matching tagged image and artifacts.
set -Eeuo pipefail
umask 077
REPO=smorad3363/WireDash
if [[ -z "${WIREDASH_SOURCE_SHA:-}" ]]; then
  command -v curl >/dev/null || { echo 'Missing curl' >&2; exit 1; }
  command -v python3 >/dev/null || { echo 'Missing python3' >&2; exit 1; }
  # Prefer the last successfully published SHA: raw.githubusercontent.com is
  # already required to download this installer. GitHub API connectivity is
  # optional (frequently unavailable from restricted VPS networks).
  tmp_ref=$(mktemp /tmp/wiredash-release.XXXXXXXX)
  tmp_installer=$(mktemp /tmp/wiredash-installer.XXXXXXXX)
  trap 'rm -f "$tmp_ref" "$tmp_installer"' EXIT
  resolved=""
  if curl --proto '=https' --tlsv1.2 -fsSL --connect-timeout 8 --max-time 30 --retry 2 \
    "https://raw.githubusercontent.com/$REPO/release-pointer/release.sha" -o "$tmp_ref"; then
    resolved=$(tr -d '\r\n' < "$tmp_ref")
    if [[ ! "$resolved" =~ ^[a-f0-9]{40}$ ]]; then
      echo '[wiredash] WARNING: invalid release-pointer SHA' >&2
      resolved=""
    fi
  fi
  # If release-pointer is temporarily unavailable, use Git to resolve main.
  # This fallback needs github.com:443 but not api.github.com.
  if [[ -z "$resolved" ]] && command -v git >/dev/null; then
    git_ref=$(git -c http.lowSpeedLimit=1024 -c http.lowSpeedTime=10 \
      ls-remote "https://github.com/$REPO.git" refs/heads/main 2>/dev/null || :)
    git_sha=${git_ref%%[[:space:]]*}
    if [[ "$git_sha" =~ ^[a-f0-9]{40}$ ]]; then resolved=$git_sha; fi
  fi
  if [[ -z "$resolved" ]]; then
    echo '[wiredash] ERROR: cannot resolve a release. Verify HTTPS to raw.githubusercontent.com (release-pointer) or github.com:443.' >&2
    echo '[wiredash] Your server may block GitHub; Ubuntu version and Docker are not the cause.' >&2
    exit 1
  fi
  curl --proto '=https' --tlsv1.2 -fsSL --connect-timeout 8 --max-time 60 --retry 2 \
    "https://raw.githubusercontent.com/$REPO/$resolved/install.sh" -o "$tmp_installer" ||
      { echo "[wiredash] ERROR: cannot download pinned installer $resolved from raw.githubusercontent.com" >&2; exit 1; }
  [[ -s "$tmp_installer" ]] ||
    { echo '[wiredash] ERROR: downloaded installer is empty' >&2; exit 1; }
  WIREDASH_SOURCE_SHA="$resolved" bash "$tmp_installer" "$@"
  exit $?
fi
[[ "$WIREDASH_SOURCE_SHA" =~ ^[a-f0-9]{40}$ ]] ||
  { echo 'Invalid release revision' >&2; exit 1; }
VERSION="sha-${WIREDASH_SOURCE_SHA}"
BASE="https://raw.githubusercontent.com/$REPO/$WIREDASH_SOURCE_SHA"
IMAGE="ghcr.io/smorad3363/wiredash:$VERSION"
DEST=/opt/wgdashboard
COMPOSE="$DEST/compose.yaml"
CONTAINER=wgdashboard
PORT=10086
TZ=Europe/Istanbul
BIND=0.0.0.0
PORT_GIVEN=0
TZ_GIVEN=0
BIND_GIVEN=0
SKIP_BACKUP=0
SKIP_WATCHDOG=0
DRY_RUN=0
UPGRADE=0
MIGRATE=0
UNINSTALL=0
STAGE=""
step() { printf '[wiredash] step %s/9: %s\n' "$1" "$2"; }
die() { echo "[wiredash] ERROR: $*" >&2; exit 1; }
log() { echo "[wiredash] $*"; }
usage() {
  cat <<EOF
Usage: sudo bash install.sh [--port N] [--tz AREA/CITY] [--panel-bind IP]
 [--skip-backup] [--skip-watchdog] [--dry-run] [--upgrade] [--migrate] [--uninstall]
Revision: $WIREDASH_SOURCE_SHA (image $IMAGE). Same command for fresh installs and managed upgrades.
EOF
}
while (($#)); do
  case "$1" in
    --port) (($# >= 2)) || die '--port needs value'; PORT=$2; PORT_GIVEN=1; shift 2 ;;
    --tz) (($# >= 2)) || die '--tz needs value'; TZ=$2; TZ_GIVEN=1; shift 2 ;;
    --panel-bind) (($# >= 2)) || die '--panel-bind needs value'; BIND=$2; BIND_GIVEN=1; shift 2 ;;
    --skip-backup) SKIP_BACKUP=1; shift ;;
    --skip-watchdog) SKIP_WATCHDOG=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --upgrade) UPGRADE=1; shift ;;
    --migrate) MIGRATE=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done
# Honor saved settings on subsequent runs, unless explicitly overridden.
# Only read expected scalar keys; never source or execute a .env file.
if [[ -f "$DEST/.env" ]]; then
  read_setting() {
    local key=$1
    sed -n "s/^${key}=//p" "$DEST/.env" | tail -n 1
  }
  if ((PORT_GIVEN==0)); then PORT=$(read_setting WGD_PORT); PORT=${PORT:-10086}; fi
  if ((TZ_GIVEN==0)); then TZ=$(read_setting WGD_TZ); TZ=${TZ:-Europe/Istanbul}; fi
  if ((BIND_GIVEN==0)); then BIND=$(read_setting WGD_PANEL_BIND); BIND=${BIND:-0.0.0.0}; fi
fi
[[ "$PORT" =~ ^[0-9]+$ ]] && ((PORT>=1 && PORT<=65535)) || die 'invalid TCP port'
[[ "$TZ" =~ ^[a-zA-Z0-9_+/-]+$ ]] || die 'invalid timezone'
[[ "$BIND" == 0.0.0.0 || "$BIND" == 127.0.0.1 ]] || die 'panel-bind must be 0.0.0.0 or 127.0.0.1'
(( UPGRADE+MIGRATE+UNINSTALL <= 1 )) || die 'choose only one of --upgrade, --migrate, --uninstall'
if ((DRY_RUN)); then
  log "DRY RUN: release=$VERSION image=$IMAGE port=$PORT bind=$BIND tz=$TZ"
  log "Planned: preflight, tagged downloads, compose deployment, health+patch checks, backup and watchdog."
  log "Existing deployments never change without --upgrade or --migrate; no files/services modified."
  exit 0
fi
((EUID == 0)) || die 'must run as root (sudo)'
if ((UNINSTALL)); then
  step 1 'uninstall helper units only (keep VPN, volumes, backups, containers)'
  for unit in wgd-watchdog.service wgdashbackup.timer wgdashbackup-health.timer wgdashbackup-panel.service; do
    systemctl disable --now "$unit" 2>/dev/null || :
  done
  rm -f /etc/systemd/system/wgd-watchdog.service \
    /etc/systemd/system/wgdashbackup.service /etc/systemd/system/wgdashbackup.timer \
    /etc/systemd/system/wgdashbackup-health.service /etc/systemd/system/wgdashbackup-health.timer \
    /etc/systemd/system/wgdashbackup-panel.service
  systemctl daemon-reload
  log 'Uninstalled helper units. VPN, container, config, archives and binaries preserved.'
  exit 0
fi
step 1 'host prerequisites'
if [[ -f /etc/os-release ]]; then
  ID=$( . /etc/os-release; printf '%s' "${ID:-}" )
  VERSION_ID=$( . /etc/os-release; printf '%s' "${VERSION_ID:-}" )
  PRETTY_NAME=$( . /etc/os-release; printf '%s' "${PRETTY_NAME:-}" )
  if [[ "${ID:-}" != ubuntu || ( "${VERSION_ID:-}" != 22.04 && "${VERSION_ID:-}" != 24.04 ) ]]; then
    log "WARNING: tested on Ubuntu 22.04/24.04; found ${PRETTY_NAME:-unknown}"
  fi
fi
for cmd in curl python3 tar gzip flock sha256sum split systemctl ss; do
  command -v "$cmd" >/dev/null || die "missing tool: $cmd"
done
install_docker() {
  [[ "${ID:-}" == ubuntu ]] || die 'Docker is missing; install Docker Engine and Compose v2 manually'
  command -v apt-get >/dev/null || die 'apt-get not found'
  log 'Installing Docker Engine and Compose v2 from official Docker apt repository'
  apt-get update
  apt-get install -y ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  local arch codename
  arch=$(dpkg --print-architecture)
  codename=$( . /etc/os-release; echo "$VERSION_CODENAME" )
  printf 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' "$codename" "$arch" > /etc/apt/sources.list.d/docker.sources
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
}
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  install_docker
fi
docker info >/dev/null || die 'Docker daemon unavailable'
docker compose version >/dev/null || die 'Docker Compose v2 unavailable'
# If a one-time migration created a compatibility symlink, keep using the
# original absolute Compose path (and therefore original Compose project).
if [[ -L "$COMPOSE" ]]; then
  COMPOSE=$(readlink -f "$COMPOSE")
  [[ -f "$COMPOSE" ]] || die 'stored Compose link is broken; refusing to modify deployment'
fi
running() { [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || :)" == true ]]; }
exists() { docker inspect "$CONTAINER" >/dev/null 2>&1; }
verify_port() {
  local port=$1 mode=$2 output=""
  if [[ "$mode" == tcp ]]; then output=$(ss -H -ltn "sport = :$port" 2>/dev/null || :); else
    output=$(ss -H -lun "sport = :$port" 2>/dev/null || :)
  fi
  [[ -z "$output" ]] || die "$mode port $port already in use; refusing to touch another listener"
}
step 2 'port and existing deployment checks'
if ((UPGRADE)) && ! exists; then die '--upgrade requires an existing wgdashboard container'; fi
if ((MIGRATE)) && ! exists; then die '--migrate requires an existing wgdashboard container'; fi
# Do not overwrite an orphaned Compose deployment just because its container is missing.
if ! exists && [[ -f "$COMPOSE" ]] && ((UPGRADE == 0 && MIGRATE == 0)); then
  die "Existing Compose config without running container; inspect before installation"
fi
if exists && ((UPGRADE == 0 && MIGRATE == 0)); then
  if running && [[ -f "$COMPOSE" ]] &&
     grep -Eq '^[[:space:]]*image:[[:space:]]+ghcr.io/smorad3363/wiredash:(sha-|v)' "$COMPOSE"; then
    if grep -Fq "$IMAGE" "$COMPOSE"; then
      log 'already installed: exact revision; no container recreation'
    else
      UPGRADE=1
      log 'Managed WireDash detected: same command performs guarded upgrade'
    fi
  else
    die "Existing '$CONTAINER' is not managed by WireDash; inspect and use --migrate explicitly"
  fi
elif ! exists; then
  verify_port "$PORT" tcp
  verify_port 51820 udp
fi
step 3 'prepare staging area'
STAGE=$(mktemp -d /tmp/wiredash.XXXXXXXX)
trap '[[ -z "${STAGE:-}" ]] || rm -rf -- "$STAGE"' EXIT
fetch() {
  local rel=$1 dest=$2
  curl --proto '=https' --tlsv1.2 -fsSL --retry 2 "$BASE/$rel" -o "$dest"
  [[ -s "$dest" ]] || die "empty release asset: $rel"
}
fetch deploy/compose.yaml "$STAGE/compose.yaml"
fetch deploy/wgdashbackup.sh "$STAGE/wgdashbackup.sh"
fetch deploy/wgdashbackup-panel-agent.py "$STAGE/wgdashbackup-panel-agent.py"
fetch deploy/wgdashbackup_import.py "$STAGE/wgdashbackup_import.py"
fetch deploy/wgdashbackup_progress.py "$STAGE/wgdashbackup_progress.py"
fetch deploy/wgdashbackup_telegram.py "$STAGE/wgdashbackup_telegram.py"
fetch deploy/wgdashbackup-panel.service "$STAGE/wgdashbackup-panel.service"
fetch deploy/wgd-watchdog.sh "$STAGE/wgd-watchdog.sh"
fetch deploy/wgd-watchdog.service "$STAGE/wgd-watchdog.service"
fetch tests/check_patch.py "$STAGE/check_patch.py"
# Prefer the CI-built immutable image. Build the same checked-out SHA locally
# if GHCR is still private/not published (the one-liner remains usable).
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  if ! docker pull "$IMAGE"; then
    log 'CI image not available; building exact GitHub revision locally'
    mkdir -p "$STAGE/source"
    curl --proto '=https' --tlsv1.2 -fsSL --connect-timeout 10 --max-time 180 --retry 2 \
      "https://codeload.github.com/$REPO/tar.gz/$WIREDASH_SOURCE_SHA" -o "$STAGE/source.tar.gz" ||
      die 'Cannot fetch source archive from codeload.github.com; check HTTPS networking or GHCR accessibility'
    tar -xzf "$STAGE/source.tar.gz" -C "$STAGE/source" --strip-components=1
    [[ -f "$STAGE/source/docker/Dockerfile" ]] || die 'source archive incomplete'
    docker build --file "$STAGE/source/docker/Dockerfile" \
      --tag "$IMAGE" "$STAGE/source" || die 'local Docker image build failed'
  fi
fi
grep -Fq '__WIREDASH_TAG__' "$STAGE/compose.yaml" || die 'template marker missing'
sed -i "s/__WIREDASH_TAG__/$VERSION/g" "$STAGE/compose.yaml"
step 4 'deployment config'
mkdir -p "$DEST"
if ((MIGRATE)); then
  exists && running || die 'migration requires running wgdashboard container'
  migrate_compose=$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' "$CONTAINER")
  [[ "$migrate_compose" == /* && -f "$migrate_compose" && "$migrate_compose" != *,* ]] || die 'cannot identify exactly one compose file for migration'
  COMPOSE="$migrate_compose"
  # wireback expects /opt/wgdashboard/compose.yaml; refuse to replace unrelated files.
  if [[ "$COMPOSE" != "$DEST/compose.yaml" && -e "$DEST/compose.yaml" ]]; then
    [[ "$(readlink -f "$DEST/compose.yaml")" == "$(readlink -f "$COMPOSE")" ]] ||
      die 'managed compose path is occupied by a different deployment; migrate manually'
  fi
  log "Migrating existing compose: $COMPOSE"
  if (( PORT_GIVEN == 0 )); then
    # On migrations, a different host port may already be published.
    PORT=$(docker inspect "$CONTAINER" | python3 -c '
import json,sys
ports=json.load(sys.stdin)[0].get("NetworkSettings",{}).get("Ports") or {}
published={binding["HostPort"]
    for container_port,bindings in ports.items()
    if container_port.endswith("/tcp") and bindings
    for binding in bindings if binding.get("HostPort")}
if len(published)!=1: raise SystemExit("expected exactly one published TCP panel port")
print(next(iter(published)))
')
    [[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1 && PORT <= 65535 )) || die 'invalid published panel port'
  fi
elif exists && ((UPGRADE)); then
  [[ -f "$COMPOSE" ]] || die 'upgrade requires existing managed compose under /opt/wgdashboard'
  [[ -f "$DEST/.env" ]] || die 'upgrade requires existing managed .env'
  log 'Upgrading managed deployment; preserving .env and volume names'
elif ! exists; then
  install -m 600 "$STAGE/compose.yaml" "$COMPOSE"
  printf 'WGD_PORT=%s\nWGD_TZ=%s\nWGD_PANEL_BIND=%s\n' "$PORT" "$TZ" "$BIND" > "$DEST/.env"
else
  log 'already done: compose retained'
fi
# Capture a checked local snapshot before changing an existing running deployment.
snapshot() {
  local target=$1
  local data wg awg tmp
  mkdir -p /var/backups/wgdashboard
  mapfile -t mounts < <(docker inspect "$CONTAINER" | python3 -c '
import json,sys
ms={m["Destination"]:m["Source"] for m in json.load(sys.stdin)[0]["Mounts"]}
for d in ("/data","/etc/wireguard","/etc/amnezia/amneziawg"):
    print(ms.get(d,""))')
  (("${#mounts[@]}" == 3)) || die 'unexpected volume layout'
  data=${mounts[0]}; wg=${mounts[1]}; awg=${mounts[2]}
  [[ -d "$data/db" && -d "$wg" && -d "$awg" && -f "$data/wg-dashboard.ini" ]] || die 'missing expected volume paths/SQLite config'
  tmp=$(mktemp -d /var/backups/wgdashboard/.pre-migrate.XXXXXXXX)
  mkdir -p "$tmp/data" "$tmp/etc/wireguard" "$tmp/etc/amnezia/amneziawg"
  cp -a "$data/." "$tmp/data/"
  cp -a "$wg/." "$tmp/etc/wireguard/"
  cp -a "$awg/." "$tmp/etc/amnezia/amneziawg/"
  # Replace live-copied SQLite files with consistent online SQLite backups.
  python3 - "$data" "$tmp/data/db" <<'PY'
import configparser,pathlib,sqlite3,sys
data=pathlib.Path(sys.argv[1]); destination=pathlib.Path(sys.argv[2]); source=data/'db'
config=configparser.RawConfigParser(strict=False)
config.read(data/'wg-dashboard.ini')
if config.get('Database','type',fallback='sqlite').lower() != 'sqlite':
    raise SystemExit('External database detected: refusing incomplete migration backup')
files=[p for p in source.rglob('*') if p.is_file() and p.suffix.lower() in ('.db','.sqlite','.sqlite3')]
if not files: raise SystemExit('No SQLite database detected; abort migration backup')
for p in files:
    dest=destination/p.relative_to(source)
    dest.parent.mkdir(parents=True,exist_ok=True)
    for s in (str(dest)+'-wal',str(dest)+'-shm',str(dest)+'-journal'):
        pathlib.Path(s).unlink(missing_ok=True)
    dest.unlink(missing_ok=True)
    with sqlite3.connect(f'file:{p}?mode=ro',uri=True,timeout=30) as src:
        with sqlite3.connect(dest) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA quick_check').fetchone()!=('ok',):
                raise SystemExit('SQLite integrity failed for '+str(p))
PY
  cp -a "$COMPOSE" "$tmp/compose.yaml"
  [[ ! -f "$DEST/.env" ]] || cp -a "$DEST/.env" "$tmp/.env"
  tar -C "$tmp" -czf "$target" .
  tar -tzf "$target" >/dev/null || die 'pre-migration archive verification failed'
  sha256sum "$target" > "$target.sha256"
  rm -rf -- "$tmp"
  log "Verified local pre-change snapshot: $target"
}
old_compose=""
if ((MIGRATE || UPGRADE)); then
  step 5 'take verified backup before deployment replacement'
  old_compose="$STAGE/previous-compose.yaml"
  cp -a "$COMPOSE" "$old_compose"
  snapshot "/var/backups/wgdashboard/pre-migrate-$(date -u +%Y%m%d-%H%M%S).tar.gz"
  # Upgrades preserve the existing Compose layout, mappings, volumes and settings.
  if ((MIGRATE || UPGRADE)); then
    python3 - "$COMPOSE" "$IMAGE" <<'PY'
import pathlib,re,sys
path=pathlib.Path(sys.argv[1]); image=sys.argv[2]
lines=path.read_text().splitlines(keepends=True)
inside=False; found=[]
for i,line in enumerate(lines):
    if re.match(r'^  wgdashboard:\s*$',line): inside=True; continue
    if inside and re.match(r'^  [A-Za-z0-9_-]+:\s*$',line): inside=False
    if inside and re.match(r'^    image:\s*\S+',line):
        found.append(i)
if len(found)!=1: raise SystemExit('Expected exactly one wgdashboard image declaration; no changes made')
i=found[0]
lines[i]=re.sub(r'^(    image:\s*).*(\r?\n)$',lambda m:m.group(1)+image+m.group(2),lines[i])
path.write_text(''.join(lines))
PY
  fi
fi
# Mount a restricted Unix control socket into the panel, never the Docker
# socket or any host backup directory. Existing Compose layouts are preserved.
install -d -m 0700 /run/wgdashbackup-panel /var/lib/wgdashbackup-import
if ! python3 - "$COMPOSE" <<'PY'
import pathlib,re,sys
p=pathlib.Path(sys.argv[1]); s=p.read_text()
mounts=[
    "      - /run/wgdashbackup-panel:/run/wgdashbackup-panel:ro",
    "      - /var/lib/wgdashbackup-import:/var/lib/wgdashbackup-import:rw",
]
# This supports both the standard WireDash layout and explicitly migrated
# existing deployments with nonstandard named volumes.
service=re.search(r"(?m)^  wgdashboard:[ \t]*$",s)
if not service:
    raise SystemExit("Cannot safely find wgdashboard service in Compose")
next_service=re.search(r"(?m)^  [A-Za-z0-9_-]+:[ \t]*$",s[service.end():])
end=service.end()+next_service.start() if next_service else len(s)
section=s[service.end():end]
volumes=re.search(r"(?m)^    volumes:[ \t]*$",section)
if not volumes:
    raise SystemExit("Cannot safely find wgdashboard volumes in Compose")
after=section[volumes.end():]
boundary=re.search(r"(?m)^    [A-Za-z0-9_-]+:",after)
volume_end=service.end()+volumes.end()+(boundary.start() if boundary else len(after))
missing=[m for m in mounts if m not in section]
if missing:
    s=s[:volume_end].rstrip("\n")+"\n"+"\n".join(missing)+"\n"+s[volume_end:]
    p.write_text(s)
PY
then
  [[ -z "$old_compose" ]] || cp -a "$old_compose" "$COMPOSE"
  die 'Cannot add restricted backup socket mount; original Compose restored'
fi
step 6 'deploy image and verify running HTTP API'
if ! docker compose -f "$COMPOSE" up -d --no-deps wgdashboard; then
  [[ -z "$old_compose" ]] || { cp -a "$old_compose" "$COMPOSE"; docker compose -f "$COMPOSE" up -d --no-deps wgdashboard || :; }
  die 'docker compose up failed (rolled back compose on upgrade/migration)'
fi
healthy=0
for ((i=0;i<36;i++)); do
  if curl -fsS --max-time 5 "http://127.0.0.1:$PORT/api/handshake" >/dev/null 2>&1; then healthy=1; break; fi
  sleep 5
done
if ((healthy==0)); then
  if [[ -n "$old_compose" ]]; then
    cp -a "$old_compose" "$COMPOSE"
    docker compose -f "$COMPOSE" up -d --no-deps wgdashboard || :
  fi
  die 'panel HTTP handshake failed after 180s; original compose restored when available'
fi
if ! docker exec -i "$CONTAINER" python3 - /opt/wgdashboard/src/modules/PeerShareLinks.py < "$STAGE/check_patch.py"; then
  if [[ -n "$old_compose" ]]; then
    cp -a "$old_compose" "$COMPOSE"
    docker compose -f "$COMPOSE" up -d --no-deps wgdashboard || :
  fi
  die 'post-install patch assertion failed; previous image restored if applicable'
fi
step 7 'install backup integration'
if ((MIGRATE)) && [[ "$COMPOSE" != "$DEST/compose.yaml" ]] && [[ ! -e "$DEST/compose.yaml" ]]; then
  ln -s "$COMPOSE" "$DEST/compose.yaml"
  log 'Created compatibility link for wireback: /opt/wgdashboard/compose.yaml'
fi
if ((MIGRATE)) && [[ ! -f "$DEST/.env" ]]; then
  # Store the probed port for every future invocation of the SAME command;
  # do not modify the legacy project's own env file.
  printf 'WGD_PORT=%s\\nWGD_TZ=%s\\nWGD_PANEL_BIND=%s\\n' "$PORT" "$TZ" "$BIND" > "$DEST/.env"
  chmod 600 "$DEST/.env"
fi
if ((SKIP_BACKUP==0)); then
  bash "$STAGE/wgdashbackup.sh" --install
  install -d -m 0700 /usr/local/libexec
  agent_changed=0
  if ! cmp -s "$STAGE/wgdashbackup-panel-agent.py" /usr/local/libexec/wgdashbackup-panel-agent.py ||
     ! cmp -s "$STAGE/wgdashbackup_import.py" /usr/local/libexec/wgdashbackup_import.py ||
     ! cmp -s "$STAGE/wgdashbackup_progress.py" /usr/local/libexec/wgdashbackup_progress.py ||
     ! cmp -s "$STAGE/wgdashbackup_telegram.py" /usr/local/libexec/wgdashbackup_telegram.py ||
     ! cmp -s "$STAGE/wgdashbackup-panel.service" /etc/systemd/system/wgdashbackup-panel.service; then
    agent_changed=1
  fi
  install -m 0700 "$STAGE/wgdashbackup-panel-agent.py" /usr/local/libexec/wgdashbackup-panel-agent.py
  install -m 0600 "$STAGE/wgdashbackup_import.py" /usr/local/libexec/wgdashbackup_import.py
  install -m 0600 "$STAGE/wgdashbackup_progress.py" /usr/local/libexec/wgdashbackup_progress.py
  install -m 0600 "$STAGE/wgdashbackup_telegram.py" /usr/local/libexec/wgdashbackup_telegram.py
  install -m 0644 "$STAGE/wgdashbackup-panel.service" /etc/systemd/system/wgdashbackup-panel.service
  systemctl daemon-reload
  systemctl enable --now wgdashbackup-panel.service
  if ((agent_changed)); then systemctl restart wgdashbackup-panel.service; fi
  systemctl is-active --quiet wgdashbackup-panel.service ||
    die 'Host backup control service failed to start; inspect journalctl -u wgdashbackup-panel'
else log 'backup skipped by request'; fi
step 8 'install HTTP watchdog'
if ((SKIP_WATCHDOG==0)); then
  desired_env=""
  if [[ "$PORT" != 10086 ]]; then desired_env="WGD_PORT=$PORT"; fi
  previous_env=""
  [[ ! -f /etc/wgd-watchdog.env ]] || previous_env=$(cat /etc/wgd-watchdog.env)
  if cmp -s "$STAGE/wgd-watchdog.sh" /usr/local/bin/wgd-watchdog.sh &&
     cmp -s "$STAGE/wgd-watchdog.service" /etc/systemd/system/wgd-watchdog.service &&
     [[ "$desired_env" == "$previous_env" ]] &&
     systemctl is-active --quiet wgd-watchdog.service &&
     systemctl is-enabled --quiet wgd-watchdog.service; then
    log 'already done: watchdog is active; no restart'
  else
    install -m 700 "$STAGE/wgd-watchdog.sh" /usr/local/bin/wgd-watchdog.sh
    install -m 644 "$STAGE/wgd-watchdog.service" /etc/systemd/system/wgd-watchdog.service
    if [[ -n "$desired_env" ]]; then
      printf '%s\n' "$desired_env" > /etc/wgd-watchdog.env
      chmod 600 /etc/wgd-watchdog.env
    else
      rm -f /etc/wgd-watchdog.env
    fi
    systemctl daemon-reload
    systemctl enable --now wgd-watchdog.service
    systemctl restart wgd-watchdog.service
  fi
else log 'watchdog skipped by request'; fi
step 9 'summary'
log "WireDash $VERSION installed/updated; panel: http://SERVER_IP:$PORT"
log 'Backup settings are now available in the admin panel. Telegram remains configurable via sudo wireback.'
log 'Watchdog logs: journalctl -u wgd-watchdog -f'
