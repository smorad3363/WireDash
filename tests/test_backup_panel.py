#!/usr/bin/env python3
"""No live WireGuard access: guard admin backup bridge authorization and host agent."""
import ast
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path("src").resolve()))

MODULE_PATH = Path("deploy/wgdashbackup-panel-agent.py")
SPEC = importlib.util.spec_from_file_location("wiredash_backup_agent", MODULE_PATH)
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


class HostAgentContract(unittest.TestCase):
    def test_archive_whitelist_and_symlinks(self):
        with tempfile.TemporaryDirectory() as root:
            agent.ARCHIVES = Path(root)
            name = "wgdashboard-20261010-030000.tar.gz"
            path = Path(root) / name
            path.write_bytes(b"synthetic-not-a-real-backup")
            self.assertEqual(agent.archive_path(name), path)
            for invalid in ("/etc/passwd", "../file", "pre-migrate-20260101-010101.tar.gz",
                            "wgdashboard-20261010-030000.tar.gz;whoami", "a"*300):
                with self.assertRaises(ValueError):
                    agent.archive_path(invalid)
            link = Path(root) / "wgdashboard-20261010-030001.tar.gz"
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                agent.archive_path(link.name)
            sidecar = Path(str(path) + ".sha256")
            sidecar.write_text("a"*64 + "  " + name + "\n")
            items = agent.list_archives()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["sha256"], "a"*64)

    def test_fixed_command_allowlist_and_parameter_validation(self):
        with self.assertRaisesRegex(ValueError, "Unknown"):
            agent.call("exec", {"operation": "exec", "command": "id"})
        for n in (0, -1, 1441, "15", True, None):
            with self.assertRaises(ValueError):
                agent.call("set_interval", {"minutes": n})
        with patch.object(agent, "execute", return_value=True) as mocked:
            with patch.object(agent, "status", return_value={"available": True}):
                self.assertTrue(agent.call("set_interval", {"minutes": 60})["available"])
                mocked.assert_called_once_with(
                    [agent.BACKUP_BIN, "--set-interval", "60"], timeout=30)

    def test_restore_requires_exact_confirmation_and_integrity(self):
        with tempfile.TemporaryDirectory() as root:
            agent.ARCHIVES = Path(root)
            name = "wgdashboard-20261010-033000.tar.gz"
            path = Path(root) / name
            path.write_bytes(b"synthetic")
            Path(str(path) + ".sha256").write_text("b"*64 + "  " + name + "\n")
            calls = []
            def runner(command, *args, **kwargs):
                calls.append(command)
                return True
            with patch.object(agent, "execute", side_effect=runner):
                with self.assertRaisesRegex(ValueError, "confirmation"):
                    agent.call("restore", {"name": name, "confirmation": "RESTORE"})
                self.assertEqual(len(calls), 1)  # verify, never queue restore
                result = agent.call("restore", {
                    "name": name, "confirmation": "RESTORE " + name})
                self.assertTrue(result["queued"])
                self.assertIn("--restore-approved", calls[-1])

    def test_socket_protocol_loopback_only(self):
        import threading
        from modules import BackupPanelBridge as bridge
        with tempfile.TemporaryDirectory() as root:
            sock = Path(root) / "control.sock"
            previous_socket = bridge.SOCKET
            try:
                bridge.SOCKET = str(sock)
                with patch.object(agent, "status", return_value={"available": True, "enabled": False}):
                    with agent.Server(str(sock), agent.Handler) as server:
                        thread = threading.Thread(target=server.serve_forever, daemon=True)
                        thread.start()
                        try:
                            reply = bridge.backup_panel_call("status")
                            self.assertTrue(reply["ok"])
                            self.assertTrue(reply["data"]["available"])
                            blocked = bridge.backup_panel_call("exec", command="echo unsafe")
                            self.assertFalse(blocked["ok"])
                        finally:
                            server.shutdown()
                            thread.join(timeout=3)
            finally:
                bridge.SOCKET = previous_socket

    def test_security_contract(self):
        dashboard = Path("src/dashboard.py").read_text()
        self.assertIn("def _backup_ui_authorized()", dashboard)
        self.assertIn('DashboardConfig.APIAccessed is False', dashboard)
        self.assertIn('request.cookies.get("authToken")', dashboard)
        self.assertIn('parsed.netloc != request.host', dashboard)
        self.assertIn('bcrypt.checkpw(', dashboard)
        self.assertIn('pyotp.TOTP(', dashboard)
        self.assertIn('"RESTORE " + name', dashboard)
        compose = Path("deploy/compose.yaml").read_text()
        self.assertIn("/run/wgdashbackup-panel:/run/wgdashbackup-panel:ro", compose)
        self.assertNotIn("/var/run/docker.sock", compose)
        installer = Path("install.sh").read_text()
        self.assertIn("wgdashbackup-panel.service", installer)
        self.assertIn("wgdashbackup-panel-agent.py", installer)
        shell = Path("deploy/wgdashbackup.sh").read_text()
        self.assertIn("--restore-approved", shell)
        self.assertIn("if [[ \"$mode\" != approved ]]", shell)
        ast.parse(Path("src/modules/BackupPanelBridge.py").read_text())
        view = Path("src/static/app/src/components/settingsComponent/wgdashboardSettings.vue").read_text()
        self.assertIn("<SystemBackupSettings>", view)


if __name__ == "__main__":
    unittest.main()
