#!/usr/bin/env python3
"""Verify API trace redaction and rotation without touching live WireDash data."""
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

# Pure sanitizer tests can execute on a minimal Python host.
if "flask" not in sys.modules:
    try:
        import flask
    except ImportError:
        flask = types.ModuleType("flask")
        flask.g = object()
        flask.request = object()
        flask.session = {}
        sys.modules["flask"] = flask

sys.path.insert(0, str(Path("src").resolve()))
from modules import ApiRequestAudit as audit


class ApiAuditTests(unittest.TestCase):
    def test_redaction_preserves_quota_date_and_identity_fingerprint(self):
        key = "private-secret-not-for-logs"
        body = {
            "Job": {
                "JobID": "job-id-123",
                "Configuration": "wg0",
                "Peer": "public-peer-xyz",
                "Field": "quota_total_data",
                "Value": '{"limit_gb":"40","weight":"1"}',
                "Action": "restrict",
                "Operator": "lgt",
            },
            "peers": ["public-peer-xyz"],
            "private_key": key,
            "preshared_key": key,
            "password": key,
            "wg-dashboard-apikey": key,
            "duration_days": 30,
            "quota_gb": 40,
        }
        safe = audit.safe_payload(body)
        serialized = json.dumps(safe)
        self.assertNotIn(key, serialized)
        self.assertNotIn("public-peer-xyz", serialized)
        self.assertEqual(safe["job"]["value"]["limit_gb"], "40")
        self.assertEqual(safe["duration_days"], 30)
        self.assertEqual(safe["quota_gb"], 40)
        self.assertEqual(safe["peers"]["count"], 1)

    def test_reject_unknown_job_value(self):
        self.assertEqual(audit.safe_rule_value("unknown", "SECRET"), "<redacted unrecognized rule>")
        self.assertEqual(audit.safe_rule_value("date", "BAD-SECRET"), "<invalid date value>")
        self.assertEqual(audit.safe_rule_value("quota_total_data", "BAD-SECRET"), "<invalid quota JSON>")

    def test_rotation_and_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(audit, "LOG_DIR", root), \
                 patch.object(audit, "LOG_FILE", root / "requests.jsonl"), \
                 patch.object(audit, "LOCK_FILE", root / ".lock"), \
                 patch.object(audit, "MAX_BYTES", 140):
                for i in range(20):
                    audit.append_event({"event": i, "text": "x" * 30})
                self.assertTrue((root / "requests.jsonl.1").is_file())
                self.assertFalse((root / "requests.jsonl.5").exists())
                self.assertEqual((root / "requests.jsonl").stat().st_mode & 0o777, 0o600)
                for log in root.glob("requests.jsonl*"):
                    for line in log.read_text().splitlines():
                        json.loads(line)

    def test_do_not_enable_without_opt_in(self):
        with patch.dict("os.environ", {"WGD_API_AUDIT": "0"}):
            self.assertFalse(audit.register_api_audit(None, "", {}, None))


if __name__ == "__main__":
    unittest.main()
