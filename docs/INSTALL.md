# WireDash integrated installer

**Release policy:** Merge only after all required CI checks pass. The command always uses the latest reviewed main commit and will build the matching image locally if the registry package is private. Live peer-continuity and restore drills still require an isolated test host.

## Canonical command — both INSTALL and UPDATE

```bash
curl -fsSL https://raw.githubusercontent.com/smorad3363/WireDash/main/install.sh | sudo bash
```

**Always use the identical command** for new deployments and WireDash-managed upgrades. It resolves the current `main` commit, fetches installer assets by that immutable revision and uses the image tagged `sha-<40-hex-commit>`. The image is pulled from GHCR or built locally from that exact source if GHCR is unavailable. On existing managed deployments it snapshots state, changes only the image declaration, validates, and rolls back on failure. An unmanaged WGDashboard is not automatically migrated; use explicit `--migrate` only after inspecting the current deployment.

Review-only invocation (same entrypoint, no system changes):

```bash
curl -fsSL https://raw.githubusercontent.com/smorad3363/WireDash/main/install.sh | sudo bash -s -- --dry-run
```

Options: `--port 10086`, `--tz Europe/Istanbul`, `--panel-bind 127.0.0.1`,
`--skip-backup`, `--skip-watchdog`, `--dry-run`, `--upgrade`, `--migrate`, `--uninstall`.
The default web port is 10086/TCP; WireGuard is 51820/UDP.
A regular invocation automatically updates WireDash-managed containers (same command). Upstream/unmanaged migration remains **explicit only**.
`--uninstall` removes helper units, not containers, volumes, backups or keys.

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

## Maintenance contract for future AI editors

The single canonical command in README, this file, and AGENTS.md MUST stay byte-for-byte identical. Only merge reviewed changes to `main`. `.github/workflows/docker.yml` must publish `sha-<commit>` images for `main` commits; `install.sh` resolves that commit and pins every download to it. Keep automatic upgrade behavior and data-preserving rollback. Update tests and all docs together; never introduce separate install and update commands.
