# WireDash stability and recovery roadmap (English)

Baseline: `main` at `58fa826862c7a17bdc6a832e1122d14de282e464`, working branch `work/stability-backup-roadmap-20260921`. Development and testing happen away from production. Production deployment, container recreation and VPN restarts require separate explicit approval.

## Evidence and limitations

- Production has three SQLite databases in WAL mode; Gunicorn was manually set to one worker / one thread; HTTP returned 200 and `wg0` was visible after reload. This does **not** prove long-term hang prevention. Container-local configuration changes may not survive recreation.
- An independent test container passed 500 API creates and 500 deletes in five batches of 100 with four concurrent clients, WAL and one Gunicorn thread; cleanup passed. This was not 500 concurrently active peers. Earlier two-thread runs produced errors from `wg-quick save`, malformed IP `'N'`, and incorrect error logging. Causality needs source review.
- Third-party sales bots cannot be changed: API-compatible server-side serialization and reconciliation are required.
- The uploaded ZIP is `wgdashbackup-v3.sh` source, **not** a production data backup. Do not upload production database, VPN private keys, Telegram credentials, `.env` or backup archives to this public repository.

## CP0 — Baseline and guardrails

Record the base SHA and risks; use a dedicated branch, small commits and a PR, with acceptance tests and rollback instructions. Never modify production as part of source development.

## CP1 — Backup and rollback

Security-audit the supplied backup v3 shell script, improve ignore rules, verify SQLite online snapshots, archive manifest, SHA-256, recovery in an isolated container and safe retention. Encrypt before off-host storage; keep tokens in root-only secrets. Publish only a secret-free script. Document `sudo wgdashbackup --backup` **after** the installer and restore tests pass. Restore may interrupt VPN traffic.

## CP2 — SQLite, concurrency and API reliability (priority)

Persistently enable WAL for all local DBs; set busy timeouts on every DB connection, review durability/checkpoint policy. Trace and protect the entire peer mutation (validation, IP allocation, DB write, runtime WireGuard update, config save, response) with an appropriate process-safe locking/transaction strategy. Do not replace `wg-quick` with an unsafe wrapper. Guarantee unique IPs/IDs, reconcile partial successes, improve secret-redacted error logs and request metrics. Persist configurable Gunicorn settings in image/Compose; one thread is the currently tested baseline, not a permanent substitute for correct locks. Acceptance: 500 creates + 500 deletes with four API clients, no errors, duplicates or leftover DB/config/runtime peers; failure-injection tests.

## CP3 — Configurable UDP ports and Internet routing

Validate WG/AWG UDP ListenPort, endpoint, Docker port publishing and firewall rules; test port conflicts, forwarding, NAT, return routing, MTU, DNS and IPv4/IPv6 as appropriate. Validate real bidirectional traffic and throughput, not handshakes alone. Do not modify production network/firewall before isolated tests and approval.

## CP4 — Independent watchdog and Telegram alerts

Separate web/API checks from VPN health, require repeated failures, send rate-limited sanitized incident/recovery/error notifications. Bounded web-only recovery must never automatically restart the whole VPN/container without explicit approval and testing; keep backup scheduler independent.

## CP5 — Packaging and release

Build a pinned image, persistent-volume Compose, idempotent installer, restore guide, smoke/stress tests and tagged rollback procedure. Target single install command after `scripts/install.sh` is implemented and verified: `git clone https://github.com/smorad3363/WireDash.git && cd WireDash && sudo bash scripts/install.sh`. **Do not run it yet: the installer does not exist.** Publish a verified backup command in README after CP1. Remove `wgd-loadtest`/test volumes only after confirming test cleanup and preserving diagnostic logs.

Each checkpoint gets its own commit, tests, rollback description and PR update. Failed gates block production rollout. Revoke the test API key disclosed in chat.