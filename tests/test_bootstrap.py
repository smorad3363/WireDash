#!/usr/bin/env python3
"""Simulate restricted hosts that can reach raw.githubusercontent.com but not api.github.com."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SHA = "e9b8b010cb96e229bb477ea711b246b9d96820d7"


class InstallerBootstrapTest(unittest.TestCase):
    def test_no_github_api_required(self):
        with tempfile.TemporaryDirectory() as d:
            tool = Path(d) / "curl"
            tool.write_text("""#!/usr/bin/env bash
set -Eeuo pipefail
destination=""
url=""
while (( $# )); do
  if [[ "$1" == "-o" ]]; then destination="$2"; shift 2; continue; fi
  [[ "$1" == https://* ]] && url="$1"
  shift
done
if [[ "$url" == *api.github.com* ]]; then
  printf 'GitHub API is intentionally blocked\\n' >&2
  exit 35
fi
case "$url" in
  */release-pointer/release.sha) printf '%s\\n' '""" + SHA + """' > "$destination" ;;
  */""" + SHA + """/install.sh) printf '#!/usr/bin/env bash\\nprintf "resolved:%s\\n" "$WIREDASH_SOURCE_SHA"\\n' > "$destination" ;;
  *) printf 'Unexpected URL %s\\n' "$url" >&2; exit 3 ;;
esac
""")
            tool.chmod(0o755)
            env = {**os.environ, "PATH": d + os.pathsep + os.environ.get("PATH", "")}
            env.pop("WIREDASH_SOURCE_SHA", None)
            result = subprocess.run(["bash", "install.sh"], env=env,
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("resolved:" + SHA, result.stdout)
            self.assertNotIn("api.github.com", result.stderr)


if __name__ == "__main__":
    unittest.main()
