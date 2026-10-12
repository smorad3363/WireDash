#!/usr/bin/env python3
"""Offline regression tests for persistent GRE-safe wg0 interface MTU."""
import importlib.util
from pathlib import Path
import os
import stat
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("ensure_wg_mtu", "docker/ensure-wg-mtu.py")
MTU = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MTU)

OLD = """[Interface]
Address = 10.0.0.1/24
PrivateKey = SECRET-PRIVATE-KEY
ListenPort = 51820
SaveConfig = true
PostUp = iptables -t nat -A POSTROUTING -j MASQUERADE

[Peer]
PublicKey = EXISTING-PEER-KEY
AllowedIPs = 10.0.0.2/32
"""


class PersistentWireGuardMtuTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / "wg0.conf"
        self.file.write_text(OLD)
        self.file.chmod(0o600)

    def test_insert_is_atomic_and_keeps_peer_keys_and_routes(self):
        self.assertTrue(MTU.persist_mtu(self.file, "1280"))
        content = self.file.read_text()
        self.assertEqual(content.splitlines()[1], "MTU = 1280")
        self.assertEqual(content.replace("MTU = 1280\n", ""), OLD)
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)
        self.assertEqual([x.name for x in self.file.parent.iterdir()], ["wg0.conf"])

    def test_idempotent_and_existing_mtu_replacement(self):
        self.assertTrue(MTU.persist_mtu(self.file, "1280"))
        self.assertFalse(MTU.persist_mtu(self.file, "1280"))
        self.file.write_text(OLD.replace("ListenPort =", "MTU = 1420\nMTU = 1400\nListenPort ="))
        self.assertTrue(MTU.persist_mtu(self.file, "1280"))
        self.assertEqual(self.file.read_text().count("MTU ="), 1)
        self.assertIn("PrivateKey = SECRET-PRIVATE-KEY", self.file.read_text())
        self.assertIn("PublicKey = EXISTING-PEER-KEY", self.file.read_text())

    def test_rejects_unsafe_mtu_without_mutation(self):
        for value in ("", "1279", "9001", "-1", "abc", "1280;rm", "01280"):
            original = self.file.read_bytes()
            with self.subTest(value=value), self.assertRaises(ValueError):
                MTU.persist_mtu(self.file, value)
            self.assertEqual(self.file.read_bytes(), original)

    def test_rejects_symlink_and_duplicate_interface(self):
        real = self.file
        linked = real.parent / "symlink.conf"
        linked.symlink_to(real)
        with self.assertRaises(ValueError):
            MTU.persist_mtu(linked, "1280")
        self.assertEqual(real.read_text(), OLD)
        self.file.write_text(OLD + "\n[Interface]\nPrivateKey = surprise\n")
        with self.assertRaises(ValueError):
            MTU.persist_mtu(self.file, "1280")

    def test_template_compose_and_entrypoint_contract(self):
        template = Path("docker/wg0.conf.template").read_text()
        self.assertIn("MTU = 1280", template)
        self.assertIn("PrivateKey =", template)
        compose = Path("deploy/compose.yaml").read_text()
        self.assertIn('wg_default_mtu: "${WGD_WG_MTU:-}"', compose)
        self.assertIn('wg_autostart: "wg0"', compose)
        entrypoint = Path("docker/entrypoint.sh").read_text()
        self.assertIn('python3 /usr/local/libexec/ensure-wg-mtu.py "$wg_default_mtu"', entrypoint)
        self.assertLess(entrypoint.index('ensure-wg-mtu.py'), entrypoint.index('start_and_monitor()'))
        dockerfile = Path("docker/Dockerfile").read_text()
        self.assertIn("COPY ./docker/ensure-wg-mtu.py /usr/local/libexec/ensure-wg-mtu.py", dockerfile)

    def test_installer_persists_mtu_and_checks_real_device(self):
        installer = Path("install.sh").read_text()
        self.assertIn("WG_MTU=1280", installer)
        self.assertIn("--mtu)", installer)
        self.assertIn("WGD_WG_MTU=%s", installer)
        self.assertIn("wg0 live MTU verification failed", installer)
        self.assertLess(installer.index("WGD_WG_MTU=%s"), installer.index("docker compose -f \"$COMPOSE\" up -d"))
        self.assertIn("read_setting WGD_WG_MTU", installer)


if __name__ == "__main__":
    unittest.main()
