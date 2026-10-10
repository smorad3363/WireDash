# WireDash integrated installer

**Release policy:** Merge only after all required CI checks pass. The command always uses the latest reviewed main commit and will build the matching image locally if the registry package is private. Live peer-continuity and restore drills still require an isolated test host.

## Canonical command — both INSTALL and UPDATE

```bash
curl -fsSL https://raw.githubusercontent.com/smorad3363/WireDash/main/install.sh | sudo bash
```

**Always use the identical command** for new deployments and WireDash-managed upgrades. It resolves the **last successfully published main commit** through `release-pointer/release.sha` on `raw.githubusercontent.com`, without depending on `api.github.com`. The Docker publish job promotes the pointer only after publishing an immutable image. If the pointer is unavailable, it can try `git ls-remote` as an alternate resolver. It fetches installer assets by that immutable revision and uses the image tagged `sha-<40-hex-commit>`. The image is pulled from GHCR or built locally from that exact source if GHCR is unavailable. On existing managed deployments it snapshots state, changes only the image declaration, validates, and rolls back on failure. An unmanaged WGDashboard is not automatically migrated; use explicit `--migrate` only after inspecting the current deployment.

Review-only invocation (same entrypoint, no system changes):

```bash
curl -fsSL https://raw.githubusercontent.com/smorad3363/WireDash/main/install.sh | sudo bash -s -- --dry-run
```

Options: `--port 10086`, `--tz Europe/Istanbul`, `--panel-bind 127.0.0.1`,
`--skip-backup`, `--skip-watchdog`, `--dry-run`, `--upgrade`, `--migrate`, `--uninstall`.
The default web port is 10086/TCP; WireGuard is 51820/UDP.
A regular invocation automatically updates WireDash-managed containers (same command). Upstream/unmanaged migration remains **explicit only**.
`--uninstall` removes helper units, not containers, volumes, backups or keys.

## GitHub HTTPS / bootstrap failures

Ubuntu 22.04 and Ubuntu 24.04 are supported/tested hosts; WireDash runs in Docker but the bootstrap executes on the host and still needs HTTPS network access. Some VPS networks block `api.github.com:443`. The canonical command no longer calls that API. It needs `raw.githubusercontent.com:443` for version resolution and assets, and `ghcr.io:443` for the preferred image. If the registry is unavailable, local Docker building additionally needs `codeload.github.com:443` and build dependency registries/GitHub. Check host egress with `curl -I --connect-timeout 8 https://raw.githubusercontent.com/` and `curl -I --connect-timeout 8 https://ghcr.io/v2/` (the latter may respond with HTTP 401, showing TLS connectivity). Missing network access cannot be corrected by switching to Ubuntu 24.04.

## Restore progress and compact portable backups

Admin-initiated full restore reports **real host milestones** in Settings, including validation, extraction, VPN shutdown, local pre-restore snapshot, volume replacement, startup and health checks. The progress is a **phase indicator**, not a prediction of elapsed time or byte-percent complete. It is written atomically by the detached systemd restore worker into a root-owned file under `/run/wgdashbackup-panel`, survives container restart and can be read by the admin session once the panel responds again. A disconnected browser shows a reconnect notice rather than triggering an automatic sign-in redirect. On an admin-initiated restore the installed Compose and environment are intentionally preserved so a historical archive cannot uninstall the backup socket/watchdog or revert the current deployment image; persistent VPN and dashboard data are still restored. Full restore remains destructive and **must only be performed with a tested external backup in a maintenance window**.

New full backups retain the standard `.tar.gz` format but use portable maximum-level `gzip -9` compression; no changes are required in Telegram multi-part assembly or older Restore archives. Where a temporary SQLite snapshot contains substantial deleted/free pages (at least 16 MiB and at least 10% of page count), the backup job optionally runs `VACUUM` **only on the snapshot copy** when enough temporary disk space is available. The actual live SQLite database is not changed. Maximum gzip takes more CPU, may slow the backup job and can only reduce archive size where data remains compressible; the actual reduction varies and is not guaranteed.

## Importing older backups from /root or via multiple-file upload

The host backup manager now automatically discovers candidate `*.tar.gz` full-server backups and consecutively numbered Telegram `.part-000` / `.part-0000` files placed directly in `/root` (read-only). In **Settings → WGDashboard Settings → Automatic Backup & Full Restore**, click **Verify & import from /root** on the detected entry. The server copies and reassembles parts into a private host staging directory, enforces a 12 GiB aggregate uncompressed-data bound, checks free disk space before extracting, and validates archive path safety (a single legitimate SQLite DB can exceed 512 MiB), checks the existing wireback TAR and SQLite integrity rules, computes its own SHA256, and atomically moves a renamed imported copy into `/var/backups/wgdashboard`; originals in `/root` are not changed. There is **no requirement to provide a separate SHA256 file** for old backups. Locally computed hashes detect subsequent modification but cannot prove the original archive came from a trusted sender; review backup provenance before restoring.

The same settings page accepts **multiple selected files** from a browser (all numbered parts together or several complete backup archives), with a total 900 MiB / 32-file cap. The upload uses a dedicated, writable, root-only staging directory `/var/lib/wgdashbackup-import` bind-mounted into the container. **Neither /root, /var/backups nor the Docker socket is exposed to the container.** The root-owned Unix socket agent alone verifies, hashes and promotes the archives; upload files are deleted after processing. The existing backup retention excludes imported copies, so future Telegram backups cannot silently prune imports. Backups with incompatible layout, nonconsecutive parts, malformed TAR, unsafe paths, bad SQLite, or decompression limits are rejected; they are never offered for restore.

Only `wgdashboard-...tar.gz` full-system archives and installer `pre-migrate-...tar.gz` with wireback-compatible contents can be recovered this way. Per-interface `.conf` or SQL-only files cannot be restored as a full system through this tool. Uploads and full restore remain authenticated admin-browser-only; existing bot APIs are not modified.

## Full backup and restore in admin settings

The admin's WGDashboard Settings page integrates the host's existing `wireback` Telegram backups. It can validate and save a Telegram bot destination, change the 5–1440-minute backup interval, enable/disable timers, queue an immediate **online** full backup, inspect local snapshot metadata, verify TAR/SQLite/SHA256 integrity and initiate a selected **full restore**. The existing per-configuration backup and all bot APIs are unchanged.

A root-owned Unix socket under `/run/wgdashbackup-panel/control.sock` is bound into the container **read-only as a directory**. A small, systemd-managed host agent supports only a fixed set of validated commands. The panel never gains the Docker socket or a writable host-root mount. Host controls require an authenticated administrator browser session, same-origin requests and an enabled panel password. Restore additionally requires the current admin password, MFA code if enabled, and the exact `RESTORE filename` confirmation. The host verifies the backup before scheduling a separate systemd restore process, which can survive panel shutdown. Restoring stops VPN and replaces data; perform only in a maintenance window, and check the host service journal afterward. Backups require Telegram configuration; only recent full backups retained locally can be restored by this UI. Historical `pre-migrate-*.tar.gz` files remain host-only recovery artifacts and cannot be restored from this UI.

The host agent and root restore path have not been exercised against a live WireGuard VPS. Treat the first deployment as an integration check and keep a verified external copy before using web-based restore.

## UI-only pagination and bot compatibility

The existing `/api/getWireguardConfigurationInfo` endpoint (and every other existing bot API route) retains its full payload and input parameters; scripts and bots continue working unchanged. The admin Vue configuration page instead queries `/api/ui/getWireguardConfigurationPage`, receiving at most 50 peers per request. Server-side search, filtering, sort and paging keep the browser from downloading 5000 full peer records on each refresh. The summary counters still reflect **all** active and restricted peers, and the graph shows the top 30 weighted peers with separate upload/download values (labeled as such). Bulk Jobs and Select Peers explicitly fetch the existing full-list API **only when opened**; they are still costly at 5000 peers but do not slow the normal page load. The single Gunicorn worker is deliberately retained because per-process peer caches, background job scheduler and share links are not safe to duplicate in multiple workers. Docker imposes no CPU cap on the live panel, so increasing a test-only `--cpus=1` limit does not allocate more production CPUs.

## Synthetic 5000-peer concurrent API load test

After the Docker image passes its normal tests, CI runs an **isolated** performance rehearsal with 5000 non-authenticating fake peer records in a temporary SQLite DB and 50 concurrent loopback HTTP clients (120 requests). The load-test route uses the **same production configuration-info response builder**, real Peer and WireguardConfiguration model serialization, and the real DB peer loader. The test image container has no host/network access, no VPN capabilities and no production mounts. It reports setup time, completed/error requests, p50/p95/p99/max latency, request throughput, CPU usage, and RSS memory in live stdout (no per-user secrets or policy factors). This is a regression/performance characterization on a disposable GitHub runner, **not** an actual authenticated production HTTP benchmark, peer handshake test, or a measurement of VPS performance.

To run the same test on the VPS without writing a single real user, fetch and run the read-only wrapper:

```bash
curl -fsSLo /tmp/wiredash-benchmark.sh https://raw.githubusercontent.com/smorad3363/WireDash/main/tests/run_synthetic_benchmark.sh
bash /tmp/wiredash-benchmark.sh 5000 50 120
```

The wrapper only inspects the currently installed immutable image tag, fetches the exact matching test script by SHA, and runs a separate resource-limited container with `--network none`, `--read-only`, temporary `/tmp` and **no production volumes** or external network. It prints `[LIVE]` CPU, RSS, completed/failed requests updates and a final `[SUMMARY]` with p50/p95/p99 latency, rate, errors, throughput and observed memory high-water mark; no peer keys or personal data are logged. Default: 5000 fake peers, 50 clients and 120 total requests. Arguments may change all three; test is capped to one CPU and 768 MiB on VPS. It is a synthetic API read/serialization test, not a production authenticated panel browser or VPN throughput test.

## Performance and polling

The configuration page no longer spends an unconditional second in the real-time traffic API: it computes MB/s from monotonic-time snapshots collected across refresh requests. First sample returns zero; later samples contain the elapsed-time average. Both WireGuard and AmneziaWG use this method. The background refresh still reads database counter rows, but reuses unchanged Peer objects instead of rebuilding peer-associated jobs/share links every 10 seconds. The peer-list API returns per-peer scalar data without repeatedly embedding the entire parent configuration object. Inactive browser tabs pause their peer-list and throughput polling; chart sampling is capped at 120 points and chart animations are disabled. No existing keys, WireGuard interface files, quota accounting counters, database schemas or Docker mounts are modified.

These are code-level optimizations; no claim of a measured speed-up on the production VPS is made until end-to-end timings are captured. The immutable Docker image build and HTTP smoke gate remain mandatory.

## Backup

Run `sudo wireback` then choose option 1 to set Telegram credentials interactively.
The original `wgdashbackup` name remains valid. A backup is an online SQLite snapshot without stopping Docker.
Retention is four delivered and four undelivered local archives, with 45 MB Telegram splitting.
Telegram timers stay disabled until credentials are configured.
`sudo wireback --status` shows timers and last successful delivery.
**Restore is different:** `--restore` intentionally stops the VPN, replaces volume data and requires `RESTORE` confirmation. Do not run on a live production server except during actual disaster recovery.

## Watchdog

`journalctl -u wgd-watchdog -f` tails actions. A 200 response from the unauthenticated
`/api/handshake` is considered up. It does not execute a database query:
**partial DB stalls can go undetected**.
After two failures the watchdog attempts Gunicorn HUP then isolated Gunicorn terminate/kill/relaunch,
capped at 3 recovery ladders per 30 minutes, with Telegram alert cooldown.
It **never** restarts Docker or the container.
If the container is gone/stopped, Docker's restart policy is responsible.
Restarting Gunicorn may leave `docker logs` tailing an old error log until the next container recreation;
read Gunicorn's current log files for diagnostics.
Cache-only `getLink` is correct under the fork's single-worker Gunicorn setting;
re-check correctness if workers increase, or if links expire while running.

## Migrate / upgrade

`sudo bash install.sh --migrate` reads the running container's Compose path from its Docker label,
takes and verifies a local archive under `/var/backups/wgdashboard`, changes **only** its
wgdashboard image line, recreates that service, checks HTTP and the source patch,
and restores its previous compose/image if startup fails.
**Note:** the pre-migration archive uses an online SQLite snapshot; non-database volume files
are copied while the VPN is running, so a restore rehearsal is required before relying on it.
Existing migrated compose files may retain their original process-only Docker HEALTHCHECK;
the systemd watchdog still uses the HTTP probe. On migration from a different Compose directory, the installer creates a compatibility symlink under /opt/wgdashboard only after successful checks; subsequent same-command upgrades dereference this link and keep using the legacy project's original Compose path.
`--upgrade` is for managed /opt/wgdashboard installations and takes the same snapshot.

## Verify before production

Run `bash tests/smoke.sh` in a disposable test VM. Each test is reported as PASS,
FAIL or NOT RUN; it does **not** claim live VPN continuity without a real connected peer.
Manually test induced hangs with `docker exec wgdashboard pkill -STOP gunicorn`,
process deaths and the three-recovery cap, verifying peer pings and `wg show`
throughout. Never do this first on a production VPN.

The fork must be checked against current upstream WGDashboard security releases
before every release merge. After every upstream merge re-run `tests/check_patch.py`,
rebuild under a **new** immutable version tag, and run the same VM gate.
Never fetch application files from the moving `main` branch during installation.

## Pinned Amnezia source revisions

The Docker build checks out `WGDashboard/amneziawg-go` at `2ac739347721a985001d71f49fb36d6fcdebe6f9` and `WGDashboard/amneziawg-tools` at `5d6179a6d0842e98dfb349c28cf1bd8e4b9d1079`. Base container image tags and OS package indices may still change; full binary reproducibility requires digest pinning plus dependency locks and is not claimed here.

## Per-peer limits and raw traffic
The Add Peers modal and legacy Add Peers page support validity in days (0 = unlimited), a shared **upload + download** quota in GB (0 = unlimited), and an admin-selected traffic weight between 0.1 and 10 (default 1). Both WireGuard and AmneziaWG use the same policy logic, including bulk creation. Policies are persisted as scheduled restrict jobs in the job database; the existing job scheduler checks them periodically rather than at packet time. Enforcement therefore has an interval delay and may exceed the threshold temporarily. A failed policy write attempts to restrict newly created peers and returns an error instead of silently provisioning unlimited service.

The metering weight applies consistently to both quota enforcement and displayed usage: **weighted upload = actual peer upload × weight; weighted download = actual peer download × weight; weighted total = weighted upload + weighted download**. The directions are never forced equal. WireGuard server-received bytes represent peer upload; server-sent bytes represent peer download. For example, 20 GB upload and 30 GB download at weight 2 display as **40 GB upload and 60 GB download, total 100 GB**. Persisted WireGuard counters stay raw, while every peer list/detail, historical peer graph, configuration billing aggregate, and client portal shows weighted quantities. The new PeerTrafficWeights table in the job database persists factors after expiry and backfills existing quota jobs on first startup. On permanent peer deletion, a neutral tombstone prevents archived quota jobs from reviving an old factor when a key is reused. Event logs and client portal omit the factor. Interface-level real-time speed charts remain explicitly marked *raw* because interface counters do not distinguish per-peer traffic. The image build compiles admin Vue sources so the UI is not stale.

## Maintenance contract for future AI editors

The single canonical command in README, this file, and AGENTS.md MUST stay byte-for-byte identical. Only merge reviewed changes to `main`. `.github/workflows/docker.yml` must publish `sha-<commit>` images for `main` commits; `install.sh` resolves that commit and pins every download to it. Keep automatic upgrade behavior and data-preserving rollback. Update tests and all docs together; never introduce separate install and update commands.
