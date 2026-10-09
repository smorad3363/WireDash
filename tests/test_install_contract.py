#!/usr/bin/env python3
"""Ensure the same installation command is used for every managed upgrade."""
from pathlib import Path
import re

COMMAND = "curl -fsSL https://raw.githubusercontent.com/smorad3363/WireDash/main/install.sh | sudo bash"
required = ["README.md", "docs/INSTALL.md", "AGENTS.md", ".github/copilot-instructions.md", "CLAUDE.md"]
for name in required:
    content = Path(name).read_text()
    if COMMAND not in content:
        raise SystemExit(f"FAIL: canonical install/update command missing in {name}")
installer=Path("install.sh").read_text()
if "if [[ -z \"${WIREDASH_SOURCE_SHA:-}\" ]]" not in installer:
    raise SystemExit("FAIL: revision bootstrap missing")
if "UPGRADE=1" not in installer or "same command performs guarded upgrade" not in installer:
    raise SystemExit("FAIL: automatic managed upgrade missing")
if "release-pointer/release.sha" not in installer:
    raise SystemExit("FAIL: raw-GitHub API-independent release resolution missing")
if "codeload.github.com" not in installer:
    raise SystemExit("FAIL: registry fallback must not depend on api.github.com")
if "https://api.github.com" in installer:
    raise SystemExit("FAIL: installer still depends on api.github.com")
if "sha-${WIREDASH_SOURCE_SHA}" not in installer:
    raise SystemExit("FAIL: immutable image tagging missing")
workflow=Path(".github/workflows/docker.yml").read_text()
if "Promote latest successfully published commit" not in workflow:
    raise SystemExit("FAIL: CI is not advancing the published release pointer")
if "sha-${{ github.sha }}" not in workflow:
    raise SystemExit("FAIL: CI does not publish SHA-pinned image")
print("PASS: canonical install/update command, pinned release and automatic upgrade")
