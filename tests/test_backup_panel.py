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
sys.path.insert(0, str(Path("deploy").resolve()))

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
            with patch.object(agent, "execute", side_effect=runner), \
                 patch.object(agent, "read_progress", return_value=None), \
                 patch.object(agent, "write_progress", return_value=None):
                with self.assertRaisesRegex(ValueError, "confirmation"):
                    agent.call("restore", {"name": name, "confirmation": "RESTORE"})
                self.assertEqual(len(calls), 1)  # verify, never queue restore
                result = agent.call("restore", {
                    "name": name, "confirmation": "RESTORE " + name})
                self.assertTrue(result["queued"])
                self.assertIn("--restore-approved", calls[-1])
                self.assertEqual(len(calls[-1][-1]), 12)

    def test_local_backup_delete_never_touches_telegram_or_last_copy(self):
        import builtins
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            first = "wgdashboard-20261010-030000.tar.gz"
            second = "wgdashboard-20261010-031000.tar.gz"
            a, b = base / first, base / second
            for p in (a,b):
                p.write_bytes(b"dummy-only-test")
            Path(str(a)+".sha256").write_text("a"*64)
            Path(str(a)+".sent").touch()
            Path(str(b)+".imported").touch()
            normal_open = builtins.open
            def isolated_locks(path, *args, **kwargs):
                if path == "/run/wgdashbackup.lock":
                    path = base / "backup.lock"
                elif path == "/run/wgdashbackup-restore.lock":
                    path = base / "restore.lock"
                return normal_open(path, *args, **kwargs)
            with (patch.object(agent,"ARCHIVES",base),
                  patch("builtins.open",side_effect=isolated_locks),
                  patch.object(agent,"read_progress",return_value=None),
                  patch.object(agent,"status",return_value={"available":True,"archives":[]})):
                with self.assertRaisesRegex(ValueError,"exact"):
                    agent.call("delete_local",{"name":first,"confirmation":"DELETE wrong"})
                with self.assertRaisesRegex(ValueError,"Invalid"):
                    agent.call("delete_local",{"name":"../../etc/shadow",
                                               "confirmation":"DELETE ../../etc/shadow"})
                result=agent.call("delete_local",{
                    "name":first,"confirmation":"DELETE "+first})
                self.assertEqual(result["deleted"],first)
                self.assertFalse(a.exists())
                self.assertFalse(Path(str(a)+".sent").exists())
                self.assertFalse(Path(str(a)+".sha256").exists())
                self.assertTrue(b.exists())
                with self.assertRaisesRegex(ValueError,"last local"):
                    agent.call("delete_local",{
                        "name":second,"confirmation":"DELETE "+second})
                # A running restore ALWAYS blocks local deletion, even if
                # a race would otherwise allow a safe last-copy operation.
                with patch.object(agent,"read_progress",return_value={"state":"running"}):
                    with self.assertRaisesRegex(ValueError,"Restore is queued"):
                        agent.call("delete_local",{
                            "name":second,"confirmation":"DELETE "+second})

    def test_local_delete_must_be_admin_password_and_mfa_gated(self):
        code=Path("src/dashboard.py").read_text()
        self.assertIn('operation in ("restore", "delete_local")',code)
        self.assertIn('("RESTORE " if operation == "restore" else "DELETE ")',code)
        self.assertIn('bcrypt.checkpw(',code)
        self.assertIn('pyotp.TOTP(',code)
        self.assertIn('if not _backup_ui_authorized():',code)
        ui=Path("src/static/app/src/components/settingsComponent/systemBackupSettings.vue").read_text()
        self.assertIn('Delete local',ui)
        self.assertIn('deleteConfirmation',ui)
        self.assertIn('MFA code (if enabled)',ui)
        self.assertNotIn("deleteMessage",Path("deploy/wgdashbackup-panel-agent.py").read_text())

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
        self.assertIn('("RESTORE " if operation == "restore" else "DELETE ") + name', dashboard)
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
