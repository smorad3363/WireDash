# CP1 audit: user-provided `wgdashbackup-v3.sh` (source only)

Status: **AUDIT IN PROGRESS — NOT APPROVED FOR INSTALLATION OR PRODUCTION RESTORE**.

## What was inspected

The supplied ZIP contains one 24,483-byte shell script and **no server data**. `bash -n` passed on the extracted script. ShellCheck, an end-to-end Telegram transfer and a complete disposable Docker restore have NOT been performed. The script uses SQLite's online backup API with `quick_check`, checks archive SHA-256 when available, snapshots `/data`, `/etc/wireguard`, `/etc/amnezia/amneziawg`, Compose and optionally `.env`, and attempts Telegram multipart upload. Backups are plain `.tar.gz`: **no encryption and no password**, by user requirement. Do not claim production migration is proven from these static checks.

## Findings requiring changes/tests

1. `backup()` calls `load_telegram` before producing an archive. This makes `sudo wgdashbackup --backup` fail before taking a LOCAL backup if Telegram is not configured. Decide/document whether local-only mode is supported; Telegram delivery must remain opt-in to the approved private chat.
2. The configuration fingerprint excludes SQLite databases while the three DB files are copied using separate online snapshots. It cannot guarantee a cross-database point-in-time snapshot for peer creation plus job creation. Require final controlled write freeze/delta sync before migration and compare peers/jobs/counters/keys afterwards.
3. The archive includes sensitive server keys, user keys, panel credentials and optionally `.env`. Never publish real archives to this public repo. Files must stay under owner-only paths with `0600` files and `0700` directories; explicitly warn that an unencrypted Telegram upload grants access to anyone with access to that chat/bot/archive.
4. `restore()` stops the Compose service and removes live mounted data before copying restored content. Test rollback and power/failure injection on a disposable instance; never run production restore from CI or without explicit approval. Ensure a failure after data removal cannot leave the operator without a documented recovery path.
5. The script assumes `/opt/wgdashboard/compose.yaml` and a container called `wgdashboard`; the repository currently documents a Compose file under `docker/compose.yaml`. Validate actual deployment path/volume names and whether the original Compose network, port mappings and host-level forwarding/NAT/firewall rules are preserved. Docker volumes alone cannot capture host routing policies.
6. `validate_archive()` refuses non-regular entries and traversal, but requires adversarial TAR fixtures and tests for duplicate normalized names and extraction behavior. Verify chunk order, each segment, archive SHA-256 and SQLite quick_check before any restore.
7. Existing Telegram backup health timer monitors successful backups, NOT HTTP/API uptime or Gunicorn worker hangs. Implement the separate web-only watchdog in CP6 with independent credentials, redacted error reports and bounded recovery.
8. A same-key, same-port, same-endpoint client migration cannot be guaranteed if the original literal public IP changes without working UDP forwarding AND return routing. Block cutover until original client profiles succeed from external networks.

## Next CP1 acceptance steps

- Publish a reviewed, secret-free shell script and installer to the development branch only, not production; prohibit tokens, private keys, SQLite files, `.env` and real TARs in Git history.
- Add isolated fixture tests for WAL online backup under concurrent writes; hostile archives; truncated multipart uploads; absent Telegram configuration; unsuccessful delivery; and archive permissions.
- Verify create, SHA-256, `quick_check`, restore, and equality of WireGuard/AmneziaWG key fingerprints, peer counts, quotas, usage, expiry, scheduler jobs and Docker/network config on a disposable instance.
- Document full rollback; only after the gate passes publish the supported `sudo wgdashbackup --backup` command in the README.

**No production changes or backup uploads are authorized by this audit.**