"""Flask + SQLite request/response audit test in a disposable environment."""
import json
import sys
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import sqlalchemy as db
from flask import Flask


sys.path.insert(0, str(Path("src").resolve()))
from modules import ApiRequestAudit as audit


class Integration(unittest.TestCase):
    def test_allow_access_reveals_missing_limits_not_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            main_engine = db.create_engine("sqlite:///" + str(root / "main.sqlite"))
            job_engine = db.create_engine("sqlite:///" + str(root / "jobs.sqlite"))
            meta = db.MetaData()
            def columns():
                return [db.Column("id", db.String, primary_key=True),
                        db.Column("name", db.String),
                        *[db.Column(x, db.Float) for x in (
                            "total_receive", "total_sent",
                            "cumu_receive", "cumu_sent")]]
            active = db.Table("wg0", meta, *columns())
            restricted = db.Table("wg0_restrict_access", meta, *columns())
            meta.create_all(main_engine)
            jm = db.MetaData()
            jobs_table = db.Table("PeerJobs", jm,
                db.Column("JobID", db.String, primary_key=True),
                db.Column("Configuration", db.String),
                db.Column("Peer", db.String),
                db.Column("Field", db.String),
                db.Column("Operator", db.String),
                db.Column("Value", db.String),
                db.Column("Action", db.String),
                db.Column("ExpireDate", db.DateTime))
            jm.create_all(job_engine)
            key = "SENSITIVE_PUBLIC_PEER_KEY"
            with main_engine.begin() as c:
                c.execute(restricted.insert().values(
                    id=key, name="test-client", total_receive=5,
                    total_sent=2, cumu_receive=1, cumu_sent=0))
            config = types.SimpleNamespace(engine=main_engine, peersTable=active,
                                         peersRestrictedTable=restricted)
            jobs = types.SimpleNamespace(engine=job_engine, peerJobTable=jobs_table)
            app = Flask("audit-test")
            app.secret_key = "test-only"
            with patch.dict("os.environ", {"WGD_API_AUDIT": "1"}), \
                 patch.object(audit, "LOG_DIR", root / "audit"), \
                 patch.object(audit, "LOG_FILE", root / "audit" / "requests.jsonl"), \
                 patch.object(audit, "LOCK_FILE", root / "audit" / ".lock"):
                self.assertTrue(audit.register_api_audit(app, "", {"wg0": config}, jobs))

                @app.post("/api/allowAccessPeers")
                def allow():
                    with main_engine.begin() as conn:
                        row = conn.execute(restricted.select().where(restricted.c.id == key)).mappings().first()
                        conn.execute(active.insert().values(**dict(row)))
                        conn.execute(restricted.delete().where(restricted.c.id == key))
                    return {"status": True, "data": {"private_key": "SECRET"}}

                response = app.test_client().post(
                    "/api/allowAccessPeers",
                    json={"peers": [key], "private_key": "SECRET_API_BODY"},
                    headers={"wg-dashboard-apikey": "SECRET_API_TOKEN",
                             "User-Agent": "external-bot/1"},
                )
                self.assertEqual(response.status_code, 200)
                logs = (root / "audit" / "requests.jsonl").read_text()
                self.assertNotIn("SECRET_API_BODY", logs)
                self.assertNotIn("SECRET_API_TOKEN", logs)
                # Public peer identifiers must be visible to compare exact bot calls.\n                self.assertIn("SENSITIVE_PUBLIC_PEER_KEY", logs)
                self.assertNotIn('"private_key": "SECRET"', logs)
                event = json.loads(logs.strip())
                self.assertEqual(event["before"][0]["state"], "restricted")
                self.assertEqual(event["after"][0]["state"], "active")
                self.assertEqual(event["after"][0]["active_limits"], [])
                self.assertTrue(event["api_status"])
                self.assertEqual(event["method"], "POST")
                self.assertEqual(event["auth_type"], "api_key")


if __name__ == "__main__":
    unittest.main()
