# WireDash repository rules — for all AI implementers

## Canonical install AND update command

```bash
curl -fsSL https://raw.githubusercontent.com/smorad3363/WireDash/main/install.sh | sudo bash
```

This **single command** is the permanent entrypoint. It must work for BOTH a clean installation and every future update of WireDash-managed installations. It must appear IDENTICALLY in README.md and docs/INSTALL.md. DO NOT propose separate install/update commands, do not require `--upgrade` for WireDash-managed deployments, and do not change the URL independently.

## Release invariants

- Entry point is `main/install.sh`. It resolves the latest successfully published reviewed `main` commit through the raw GitHub `release-pointer/release.sha`, then runs the pinned version of itself. The Docker publish workflow promotes this pointer only after publishing its immutable image. Never reintroduce a hard dependency on `api.github.com` into host bootstrap.
- All install-time files are fetched from exactly that commit; image is `ghcr.io/smorad3363/wiredash:sha-<40-character-commit-sha>`.
- GitHub Actions builds and publishes the same commit-tagged image on `main` pushes. Installer may build that same immutable commit locally as fallback if registry access is unavailable. Never silently fall back to `:latest`.
- Existing WireDash installations are upgraded by the SAME canonical command with verified snapshot and rollback. Legacy upstream WGDashboard migration is **explicit** and must not happen silently.
- Preserve `wgdashboard` name, WireGuard keys, three volumes and UDP/TCP mappings. Do not restart the container in watchdog/backup, do not use `compose down -v`.
- A changed installer must be reviewed along with `README.md`, `docs/INSTALL.md`, `.github/workflows/docker.yml`, Compose and CI tests. Keep the canonical command synchronized **byte-for-byte**.
- Bash syntax, ShellCheck, AST patch guard, Compose config, actual container HTTP health smoke, backup restore rehearsal, and VPN continuity must be reported PASS/FAIL/NOT RUN accurately. Never claim production safety without real peer continuity tests.
- Do not leak Telegram tokens, VPN keys, API keys or credentials in commit history, logs or terminal output.

Do not merge a change that breaks the common install/update command. New automation or AI contributors must read this file first.
