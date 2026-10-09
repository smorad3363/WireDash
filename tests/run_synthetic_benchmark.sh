#!/usr/bin/env bash
# Offline benchmark for the installed WireDash image; never changes live VPN state.
set -Eeuo pipefail
umask 077
PEERS=${1:-5000}
CONCURRENCY=${2:-50}
REQUESTS=${3:-120}
for count in "$PEERS" "$CONCURRENCY" "$REQUESTS"; do
  [[ "$count" =~ ^[0-9]+$ ]] || { echo 'Arguments must be integers' >&2; exit 2; }
done
(( PEERS >= 1 && PEERS <= 10000 && CONCURRENCY >= 1 && CONCURRENCY <= 100 &&
   REQUESTS >= 1 && REQUESTS <= 2000 )) || {
  echo 'Allowed: peers 1..10000, concurrency 1..100, requests 1..2000' >&2
  exit 2
}
command -v docker >/dev/null || { echo 'Docker not installed' >&2; exit 1; }
command -v curl >/dev/null || { echo 'curl not installed' >&2; exit 1; }
image=$(docker inspect --format '{{.Config.Image}}' wgdashboard)
if [[ ! "$image" =~ ^ghcr\.io/smorad3363/wiredash:sha-([0-9a-f]{40})$ ]]; then
  echo 'Managed WireDash image SHA unavailable; refusing to run' >&2
  exit 1
fi
revision=${BASH_REMATCH[1]}
tmp=$(mktemp /tmp/wiredash-benchmark.XXXXXXXX.py)
trap 'rm -f "$tmp"' EXIT
curl --proto '=https' --tlsv1.2 -fsSL --retry 2 --connect-timeout 8 --max-time 45 \
  "https://raw.githubusercontent.com/smorad3363/WireDash/$revision/tests/benchmark_synthetic_api.py" -o "$tmp"
[[ -s "$tmp" ]] || { echo 'Empty test script' >&2; exit 1; }
echo '[BENCH] Synthetic clients only. Live VPN and database untouched.'
docker run --rm --network none --read-only --cpus=1 --memory=768m --pids-limit=200 \
  --tmpfs /tmp:rw,nosuid,nodev,size=192m \
  --mount "type=bind,src=$tmp,dst=/tests/benchmark_synthetic_api.py,readonly" \
  -e PYTHONPATH=/opt/wgdashboard/src -w /opt/wgdashboard/src \
  --entrypoint /opt/wgdashboard/src/venv/bin/python3 "$image" \
  /tests/benchmark_synthetic_api.py --peers "$PEERS" \
  --concurrency "$CONCURRENCY" --requests "$REQUESTS"
