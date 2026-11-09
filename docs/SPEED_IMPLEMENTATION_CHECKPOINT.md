# Speed policy release validation

This branch implements the plan `wiredash-fup-renewal-20261109-v1`.
See [SPEED_POLICY.md](SPEED_POLICY.md) for actual behavior, limitations and controls.
The final production SHA and post-upgrade results are recorded separately after
release; this document does not claim deployment before it is verified.

Implemented: fixed independent directional periods and caps, inherit/custom/
exempt, master stop, revision-protected preserve/overwrite confirmation,
reset/expiry journaling and observed renewal correlation, safe kernel-state
reconciliation, account history, card usage/status, backup/restore and rename
integration. Existing bot handlers remain AST-identical. Tests use disposable
accounts, databases, namespaces and generated keys.

Validation before release:

- Pure policy, SQLite persistence, history/correlation and private auth tests.
- Full application test on isolated SQLite snapshots and generated WireGuard
  interface: account create, custom settings, reset/cap removal, expiry extension,
  renewal history, API-only denial, restrict/allow/delete, cleanup.
- Encrypted TCP about 7.73/1.93 Mbps for 8/2 Mbps lab rates; UDP 7.86/2.00 Mbps.
- Concurrent uncapped authenticated control peer over 1 Gbps on the isolated
  underlay. This does not represent throughput over an Iran/foreign tunnel.
- Process restart and injected interrupted filter install restored caps;
  removing the policy restored uncapped traffic without changing VPN peers.
- 5000-account initialization 6.22s, subsequent sample 1.75s, 50 concurrent
  history readers 0.09s, 2.22 MB SQLite database in the local test environment.
- Existing creation-order/peer-state/overlap/traffic-ledger/deletion/legacy API
  and performance runtime tests passed in a disposable image container.
- Frontend production build passed. A local Chromium launch was blocked by the
  execution environment's socket restrictions; do not label visual browser
  verification PASS on the basis of compilation alone.
- Canonical install/update, bootstrap, API AST guard, WAL resilience, rotating
  audit backup capture, log/disk bounds and MTU checks passed.
- Backup shell rehearsal needs sudo and is executed on the authorized server
  or the disposable CI runner, not inferred from an unavailable local sudo.

Production must keep master disabled until reduced Mbps values are explicitly
configured. Existing 30/30 GB thresholds and 30-day duration are prefilled.
No user traffic/reset/expiry changes are permitted just to force tests to pass.
