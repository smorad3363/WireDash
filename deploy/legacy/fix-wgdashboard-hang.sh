#!/usr/bin/env bash
#
# fix-wgdashboard-hang.sh
#
# Auto-detects a running WGDashboard docker container, backs up the
# PeerShareLinks.py module, patches the N+1 query bug in getLink()
# that causes multi-second hangs (panel timeouts, API errors when
# adding peers), verifies the patch, restarts the container, and
# commits a patched image so the fix survives container recreation.
#
# Safe to re-run: it detects if the patch is already applied and
# skips re-patching.
#
# Usage:
#   sudo bash fix-wgdashboard-hang.sh
#   sudo bash fix-wgdashboard-hang.sh --container mycontainer
#   sudo bash fix-wgdashboard-hang.sh --no-commit      (skip docker commit)
#   sudo bash fix-wgdashboard-hang.sh --dry-run        (detect only, no changes)
#
set -uo pipefail

# ---------- config / args ----------
CONTAINER_OVERRIDE=""
DO_COMMIT=1
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --container) CONTAINER_OVERRIDE="$2"; shift 2 ;;
    --no-commit) DO_COMMIT=0; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^#//'
      exit 0
      ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

TARGET_FILE="/opt/wgdashboard/src/modules/PeerShareLinks.py"
LOG_PREFIX="[wgdashboard-fix]"

log()  { echo "${LOG_PREFIX} $*"; }
err()  { echo "${LOG_PREFIX} ERROR: $*" >&2; }
die()  { err "$*"; exit 1; }

# ---------- 0. sanity checks ----------
command -v docker >/dev/null 2>&1 || die "docker command not found on this host."

if ! docker info >/dev/null 2>&1; then
  die "Cannot talk to the docker daemon (are you root / in the docker group?)."
fi

# ---------- 1. find the WGDashboard container ----------
find_container() {
  local candidate

  # 1a. explicit override wins
  if [[ -n "$CONTAINER_OVERRIDE" ]]; then
    if docker inspect "$CONTAINER_OVERRIDE" >/dev/null 2>&1; then
      echo "$CONTAINER_OVERRIDE"
      return 0
    else
      die "Container '$CONTAINER_OVERRIDE' not found."
    fi
  fi

  # 1b. common name guesses (fast path, no exec needed)
  for candidate in wgdashboard wg-dashboard wireguard-dashboard wgdashboard-1; do
    if docker inspect "$candidate" >/dev/null 2>&1; then
      if docker exec "$candidate" test -f "$TARGET_FILE" >/dev/null 2>&1; then
        echo "$candidate"
        return 0
      fi
    fi
  done

  # 1c. scan every running container and check if the target file exists inside it.
  # This is the "smart" fallback that works regardless of naming on any server.
  local id name
  while read -r id; do
    name=$(docker inspect --format '{{.Name}}' "$id" 2>/dev/null | sed 's#^/##')
    [[ -z "$name" ]] && continue
    if docker exec "$id" test -f "$TARGET_FILE" >/dev/null 2>&1; then
      echo "$name"
      return 0
    fi
  done < <(docker ps --format '{{.ID}}')

  return 1
}

log "Looking for the WGDashboard container..."
CONTAINER="$(find_container)" || die "Could not find any running container containing $TARGET_FILE. Pass it explicitly with --container <name>."
log "Found container: $CONTAINER"

# ---------- 2. locate python3 inside the container ----------
PYBIN=""
for candidate in python3 python; do
  if docker exec "$CONTAINER" sh -c "command -v $candidate" >/dev/null 2>&1; then
    PYBIN="$candidate"
    break
  fi
done
[[ -z "$PYBIN" ]] && die "No python3/python interpreter found inside container '$CONTAINER'. Cannot safely patch."
log "Using interpreter inside container: $PYBIN"

# ---------- 3. check current patch state ----------
# The buggy version calls self.__getSharedLinks() as the first line of getLink().
# The patched version does not. We detect this by inspecting the method body.
PATCH_STATE="$(docker exec "$CONTAINER" "$PYBIN" - <<'PYEOF'
import re, sys

path = "/opt/wgdashboard/src/modules/PeerShareLinks.py"
try:
    with open(path) as f:
        content = f.read()
except Exception as e:
    print("FILE_READ_ERROR:" + str(e))
    sys.exit(0)

m = re.search(
    r"def getLink\(self, Configuration: str, Peer: str\) -> list\[PeerShareLink\]:\n((?:.*\n)*?)(?=\n?    def )",
    content
)
if not m:
    print("PATTERN_NOT_FOUND")
    sys.exit(0)

body = m.group(1)
if "self.__getSharedLinks" in body or "self._PeerShareLinks__getSharedLinks" in body:
    print("UNPATCHED")
else:
    print("PATCHED")
PYEOF
)"

log "Current state: $PATCH_STATE"

case "$PATCH_STATE" in
  PATCHED)
    log "getLink() already patched. Nothing to do for the code fix."
    ;;
  FILE_READ_ERROR*)
    die "Could not read $TARGET_FILE inside container: ${PATCH_STATE#FILE_READ_ERROR:}"
    ;;
  PATTERN_NOT_FOUND)
    err "Could not locate the getLink() method with the expected signature."
    err "This container's WGDashboard version may differ from the one this script targets."
    err "No changes made. Please inspect $TARGET_FILE manually."
    exit 2
    ;;
  UNPATCHED)
    log "Bug pattern detected (getLink() re-queries the DB on every call)."
    ;;
  *)
    die "Unexpected detection result: $PATCH_STATE"
    ;;
esac

if [[ "$DRY_RUN" -eq 1 ]]; then
  log "Dry-run mode: stopping here, no changes made."
  exit 0
fi

if [[ "$PATCH_STATE" == "PATCHED" ]]; then
  log "Exiting (already patched)."
  exit 0
fi

# ---------- 4. backup ----------
BACKUP_SUFFIX=".bak.$(date +%Y%m%d%H%M%S)"
log "Backing up original file to ${TARGET_FILE}${BACKUP_SUFFIX} ..."
docker exec "$CONTAINER" cp "$TARGET_FILE" "${TARGET_FILE}${BACKUP_SUFFIX}" \
  || die "Backup failed, aborting before touching the file."

# ---------- 5. apply the patch ----------
log "Applying patch..."
PATCH_RESULT="$(docker exec "$CONTAINER" "$PYBIN" - <<'PYEOF'
import re

path = "/opt/wgdashboard/src/modules/PeerShareLinks.py"
with open(path) as f:
    content = f.read()

pattern = re.compile(
    r"(def getLink\(self, Configuration: str, Peer: str\) -> list\[PeerShareLink\]:\n)"
    r"(\s*)self\.__getSharedLinks\(\)\n"
)

new_content, n = pattern.subn(r"\1", content, count=1)

if n == 0:
    print("NO_MATCH")
else:
    with open(path, "w") as f:
        f.write(new_content)
    print("OK")
PYEOF
)"

if [[ "$PATCH_RESULT" != "OK" ]]; then
  die "Patch substitution failed (result: $PATCH_RESULT). Restoring backup..."
fi
log "Patch written."

# ---------- 6. verify syntax ----------
log "Verifying Python syntax..."
if ! docker exec "$CONTAINER" "$PYBIN" -m py_compile "$TARGET_FILE"; then
  err "Syntax check FAILED. Restoring original file from backup."
  docker exec "$CONTAINER" cp "${TARGET_FILE}${BACKUP_SUFFIX}" "$TARGET_FILE"
  die "Restored original file. No changes were kept."
fi
log "Syntax OK."

# ---------- 7. re-verify patch actually took effect ----------
RECHECK="$(docker exec "$CONTAINER" "$PYBIN" - <<'PYEOF'
import re
path = "/opt/wgdashboard/src/modules/PeerShareLinks.py"
with open(path) as f:
    content = f.read()
m = re.search(
    r"def getLink\(self, Configuration: str, Peer: str\) -> list\[PeerShareLink\]:\n((?:.*\n)*?)(?=\n?    def )",
    content
)
body = m.group(1) if m else ""
print("PATCHED" if "self.__getSharedLinks" not in body else "UNPATCHED")
PYEOF
)"

if [[ "$RECHECK" != "PATCHED" ]]; then
  err "Post-patch verification failed. Restoring backup."
  docker exec "$CONTAINER" cp "${TARGET_FILE}${BACKUP_SUFFIX}" "$TARGET_FILE"
  die "Restored original file. No changes were kept."
fi
log "Patch verified successfully."

# ---------- 8. restart container ----------
log "Restarting container '$CONTAINER' to apply the fix..."
docker restart "$CONTAINER" >/dev/null || die "docker restart failed."

log "Waiting for gunicorn to come back up..."
UP=0
for i in $(seq 1 15); do
  sleep 2
  if docker exec "$CONTAINER" pgrep -f gunicorn >/dev/null 2>&1; then
    UP=1
    break
  fi
done

if [[ "$UP" -eq 1 ]]; then
  log "gunicorn process detected, container is back up."
else
  err "Could not confirm gunicorn is running after restart. Check manually with: docker logs $CONTAINER"
fi

# ---------- 9. commit patched image for persistence ----------
if [[ "$DO_COMMIT" -eq 1 ]]; then
  IMAGE_TAG="wgdashboard-patched:$(date +%Y%m%d)"
  log "Committing patched container to image '$IMAGE_TAG' so the fix survives recreation..."
  if docker commit "$CONTAINER" "$IMAGE_TAG" >/dev/null; then
    log "Image committed: $IMAGE_TAG"
    log "Remember to point your docker-compose.yml 'image:' at this tag (or re-run this"
    log "script after any future container recreation / image update)."
  else
    err "docker commit failed. The running container is fixed, but the fix will be lost"
    err "if the container is recreated. Commit manually or re-run this script after that."
  fi
else
  log "Skipping docker commit (--no-commit given). The running container is fixed, but"
  log "the fix will be lost if the container is recreated."
fi

log "Done. Backup kept at: ${TARGET_FILE}${BACKUP_SUFFIX} (inside the container)."
log "Suggested next check: watch /opt/wgdashboard/src/log/error_*.log for [TIMING] getPeers"
log "entries and confirm they are now well under 1s (previously 10-20s+)."