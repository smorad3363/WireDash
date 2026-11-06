#!/usr/bin/env python3
"""Exercise actual backup capture shell functions with disposable volumes."""
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    repo = Path(__file__).resolve().parents[1]
    source = (repo / "deploy/wgdashbackup.sh").read_text()
    start = source.index("\nfingerprint() {") + 1
    end = source.index("\nonline_sqlite() {", start)
    function_source = source[start:end]
    assert "capture_live_files()" in function_source
    assert "api-audit" in function_source

    with tempfile.TemporaryDirectory(prefix="wiredash-capture-") as temp:
        root = Path(temp)
        data, wg, awg = (root / x for x in ("data", "wireguard", "amnezia"))
        for d in (data / "db", data / "api-audit", wg, awg):
            d.mkdir(parents=True)
        (data / "wg-dashboard.ini").write_text("[Server]\napp_port=10086\n")
        (data / "api-audit/requests.jsonl").write_text('{"method":"GET"}\n')
        (wg / "wg0.conf").write_text("[Interface]\nAddress=10.0.0.1/24\n")
        (awg / "AmneziWg.conf").write_text("[Interface]\nAddress=10.0.2.1/24\n")
        compose = root / "compose.yaml"
        compose.write_text("services:\n  wgdashboard: {}\n")
        stage = root / "stage"
        for d in (stage / "data/db", stage / "etc/wireguard", stage / "etc/amnezia/amneziawg"):
            d.mkdir(parents=True)

        code = """
set -Eeuo pipefail
log() { echo "$*" >&2; }
fail() { echo "FAILED: $*" >&2; return 1; }
""" + function_source + """
if [[ "$SCENARIO" == "audit_grows" ]]; then
  (for i in $(seq 1 180); do
    printf '{"event":%s}\\n' "$i" >> "$DATA/api-audit/requests.jsonl"
    sleep .003
  done) &
  writer=$!
fi
if [[ "$SCENARIO" == "single_change" || "$SCENARIO" == "constant_change" ]]; then
  cp() {
    command cp "$@"
    if [[ "$1" == "-a" && "$2" == "$WG/." ]]; then
      if [[ "$SCENARIO" == "constant_change" || ! -e "$ROOT/once" ]]; then
        echo "# new peer" >> "$WG/wg0.conf"
        touch "$ROOT/once"
      fi
    fi
  }
fi
capture_live_files "$STAGE"
cmp "$WG/wg0.conf" "$STAGE/etc/wireguard/wg0.conf"
cmp "$AWG/AmneziWg.conf" "$STAGE/etc/amnezia/amneziawg/AmneziWg.conf"
cmp "$COMPOSE" "$STAGE/compose.yaml"
test -f "$STAGE/data/api-audit/requests.jsonl"
test -f "$STAGE/data/wg-dashboard.ini"
if [[ "$SCENARIO" == "audit_grows" ]]; then wait "$writer"; fi
"""
        env = os.environ.copy()
        env.update(
            DATA=str(data), WG=str(wg), AWG=str(awg), COMPOSE=str(compose),
            STAGE=str(stage), ROOT=str(root),
        )
        for scenario, expected in (
            ("audit_grows", True), ("single_change", True),
            ("constant_change", False),
        ):
            (wg / "wg0.conf").write_text("[Interface]\nAddress=10.0.0.1/24\n")
            (root / "once").unlink(missing_ok=True)
            env["SCENARIO"] = scenario
            result = subprocess.run(
                ["bash", "-c", code], env=env, capture_output=True, text=True,
                timeout=20,
            )
            if (result.returncode == 0) != expected:
                raise AssertionError(scenario + ": " + result.stderr[-1200:])
            if scenario == "single_change":
                assert "attempt 1/3" in result.stderr, result.stderr
            if scenario == "constant_change":
                assert "all 3 short copy attempts" in result.stderr, result.stderr

    print("PASS: rotating audit is nonblocking; real config changes retry or fail closed")


if __name__ == "__main__":
    main()
