# WireDash implementation roadmap — English

Base `main`: `58fa826862c7a17bdc6a832e1122d14de282e464`; branch `work/stability-backup-roadmap-20260921`; draft PR #1. **Per-subscription device/user count is explicitly out of scope.** Production deployment, test-container deletion, firewall changes or VPN restarts require separate approval.

## Verified baseline and limits

Production was observed with all three SQLite databases in WAL mode, one Gunicorn worker/one thread, healthy HTTP and `wg0`; long-term hang prevention is NOT proven. An isolated test container passed 500 creates + 500 deletes in five batches of 100 with four concurrent clients, WAL and a single server thread. It did not hold 500 simultaneous active peers. With two threads, `wg-quick save` failures and malformed IP errors occurred; exact causation is not yet proven. Sales bots must remain compatible and unchanged. Uploaded ZIP contains backup SCRIPT SOURCE only, not production user data.

## CP0 — Baseline and change control

Pin source SHA, branch and draft PR; small commits, tests and rollback. No production load testing or deployment without renewed approval.

## CP1 — Unencrypted backups and recovery

Audit `wgdashbackup-v3.sh` for actual volumes, online SQLite snapshots, quick_check, SHA-256, hostile archive handling, configs/ports, retention, Telegram delivery, and isolated restore. **Archives MUST be unencrypted and password-free**, with private directories `0700`, archive files `0600`, and explicitly approved private storage/delivery. Sending an unencrypted archive grants whoever can access it the contained VPN credentials; never upload actual archives, `.env`, keys or tokens to the PUBLIC repository. Publish only secret-free source. `sudo wgdashbackup --backup` becomes supported only after install and restore verification; production restoration may interrupt VPN and requires separate approval.

**Gate:** backup/verify/restore and data/key comparisons pass on a disposable instance, with documented rollback.

## CP2 — SQLite performance, concurrency and uptime (urgent)

Persist WAL and per-connection busy timeout; review transactions, durability and checkpointing. Interprocess/thread-safe locking over the ENTIRE peer create/delete/update/IP-allocation/DB/runtime/config-save operation; reconcile partial success and prevent IP duplicates. Correct malformed logging and redact secrets. Persist Gunicorn configuration; one thread is a tested mitigation, NOT a complete concurrency fix. **In progress:** `DatabaseConnection.py` startup WAL and per-connection timeout implementation with isolated regression test; full server/load validation pending.

**Gate:** 500 creates/deletes with four clients and no failed API requests, collisions, stale peers in API/DB/config/runtime; fault injection and extended HTTP tests.

## CP3 — Quota and expiry AT peer creation

Add optional data quota and expiry date/time inputs to new-peer UI and API; establish the corresponding scheduled jobs at creation, reconcile/rollback partial failures, preserve later edits and restart/backup/restore semantics. Bots using the existing API payload must still work.

## CP4 — Custom UDP ports and real WG/AWG routing

Synchronize ListenPort, endpoint, Docker UDP publication and firewall; check port conflicts, IPv4 forwarding, NAT/return route, DNS, MTU and IPv6 where relevant. Confirm real Internet traffic and throughput, not just a handshake. No production firewall or container changes without isolated proof and approval.

## CP5 — Migration with ORIGINAL unchanged client profiles (hard gate)

Snapshot and compare 100% of server/peer identities, keys, PSKs, IPs/AllowedIPs, AWG parameters, ports, all databases, quota/usage/expiry/jobs and Docker/network settings. Do NOT regenerate keys. Clients with a literal old server IP require preserving that public IP or correctly tested UDP forwarding including the return path. DNS changes cannot repair literal-IP profiles. Freeze/drain writes or delta-sync at cutover; validate original client configs from real external networks, Internet/DNS/speed, all account state and rollback. If old endpoint continuity or working clients cannot be demonstrated, BLOCK cutover rather than promise the impossible.

## CP6 — Independent watchdog and Telegram incidents

Separate web/API and VPN monitoring; repeated failures, bounded web-only recovery with cooldown, rate-limited failure/recovery notifications, sanitized error excerpts and no exposed credentials. Sending an **unencrypted backup** is a separately authorized private-destination action, never part of automatic error-log reporting. No automatic full VPN restart without explicit testing and approval.

## CP7 — One-command install, documentation and release

Pinned Docker image, persistent volumes, configurable ports, idempotent install/upgrade, preflight, bilingual README, backup/verify/restore and versioned rollback, CI and real old-client migration tests. Do NOT publish install/backup commands as ready before tested scripts exist. Remove test container and its three volumes only after approval and log retention.

Each CP has a commit, test results, acceptance gate, rollback and PR status. Working-branch changes never deploy automatically to production.