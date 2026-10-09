# WireDash AI developer guidance

Follow [AGENTS.md](AGENTS.md) as the authoritative contract, including avoiding unexpected VPN restarts or data loss.

## One command for installation and update (do not split)

```bash
curl -fsSL https://raw.githubusercontent.com/smorad3363/WireDash/main/install.sh | sudo bash
```

Never change only one copy of this command: update README.md, docs/INSTALL.md, AGENTS.md, .github/copilot-instructions.md and tests together. All downstream resources must be pinned to a single source SHA; a rerun updates existing managed WireDash installations.
