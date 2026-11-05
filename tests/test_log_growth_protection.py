"""Regression checks for Docker/Gunicorn log retention and background-job backoff."""
import ast
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LogGrowthProtectionTests(unittest.TestCase):
    def test_failed_job_scheduler_backs_off_and_logs_once(self):
        source = (ROOT / "src/dashboard.py").read_text()
        tree = ast.parse(source)
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == "peerJobScheduleBackgroundThread")
        scope = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "<isolated>", "exec"), scope)
        calls = []
        class StopTest(Exception):
            pass
        class Logger:
            def info(self, message):
                pass
            def exception(self, message):
                calls.append(("exception", message))
        class Context:
            def __enter__(self):
                return None
            def __exit__(self, *args):
                return False
        def pause(seconds):
            calls.append(("sleep", seconds))
            if seconds == 60:
                raise StopTest
        def bad_job():
            calls.append(("run", None))
            raise RuntimeError("simulated read-only JobLog failure")
        scope.update(app=types.SimpleNamespace(app_context=Context, logger=Logger()),
                     threading=types.SimpleNamespace(get_native_id=lambda: 99),
                     time=types.SimpleNamespace(sleep=pause),
                     AllPeerJobs=types.SimpleNamespace(runJob=bad_job))
        with self.assertRaises(StopTest):
            scope["peerJobScheduleBackgroundThread"]()
        self.assertEqual([v for k, v in calls if k == "sleep"], [10, 60])
        self.assertEqual(sum(k == "run" for k, _ in calls), 1)
        self.assertEqual(sum(k == "exception" for k, _ in calls), 1)

    def test_fresh_compose_has_bounded_json_logging_and_rotatable_app_logs(self):
        text = (ROOT / "deploy/compose.yaml").read_text()
        self.assertIn('max-size: "10m"', text)
        self.assertIn('max-file: "3"', text)
        self.assertIn('/var/log/wiredash-app:/opt/wgdashboard/src/log:rw', text)

    def test_existing_compose_upgrade_adds_logging_once(self):
        installer = (ROOT / "install.sh").read_text()
        snippet = installer.split("python3 - \"$COMPOSE\" <<'PYLOG'\n", 1)[1].split("\nPYLOG", 1)[0]
        sample = """services:
  wgdashboard:
    image: some-image
    container_name: wgdashboard
    environment:
      a: b
    volumes:
      - data:/data
volumes:
  data:
"""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "compose.yaml"
            path.write_text(sample)
            for _ in range(2):
                subprocess.run([sys.executable, "-c", snippet, str(path)],
                               check=True, capture_output=True, text=True)
            updated = path.read_text()
        self.assertEqual(updated.count('max-size: "10m"'), 1)
        self.assertEqual(updated.count('max-file: "3"'), 1)
        self.assertIn("    environment:", updated)
        self.assertIn("    volumes:", updated)

    def test_installer_configures_host_rotation(self):
        installer = (ROOT / "install.sh").read_text()
        self.assertIn("copytruncate", installer)
        self.assertIn("/etc/logrotate.d/wiredash-app", installer)
        self.assertIn("/etc/cron.d/wiredash-app-logs", installer)
        self.assertIn("systemctl enable --now cron", installer)
        self.assertIn('/var/log/wiredash-app:/opt/wgdashboard/src/log:rw', installer)


if __name__ == "__main__":
    unittest.main()

