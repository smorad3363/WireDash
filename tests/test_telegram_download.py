#!/usr/bin/env python3
"""Offline Telegram link parsing and wireback part-matching security tests.

No Telegram network, user session, VPS, or VPN interfaces are needed.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import importlib.util
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path("deploy").resolve()))
SPEC=importlib.util.spec_from_file_location("wgdashbackup_telegram", "deploy/wgdashbackup_telegram.py")
tg=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tg)

NAME="wgdashboard-20261010-010336.tar.gz"
SHA="a"*64


def message(number, total=7, sha=SHA, msgid=None, suffix=4, filename=None):
    name=filename or (
        NAME if total == 1 else NAME + ".part-" + str(number-1).zfill(suffix)
    )
    return SimpleNamespace(
        id=msgid or number,
        raw_text=f"WGDashboard {NAME} | PART {number}/{total} | SHA256 {sha}",
        file=SimpleNamespace(name=name, size=45000000),
        document=True,
    )


class TelegramTests(unittest.TestCase):
    def test_public_private_and_bot_links(self):
        self.assertEqual(tg.parse_link("https://t.me/BackupChannel/923"),
                         ("username", "BackupChannel", 923))
        self.assertEqual(tg.parse_link("https://t.me/c/123456/334455"),
                         ("channel", 123456, 334455))
        self.assertEqual(tg.parse_link("tg://openmessage?user_id=1234567&message_id=44"),
                         ("user", 1234567, 44))

    def test_link_parser_does_not_fetch_urls_or_allow_ssrf(self):
        for link in (
            "http://t.me/channel/42", "https://t.me.evil.example/channel/42",
            "https://127.0.0.1/backup", "file:///root/file.tar.gz",
            "https://t.me@127.0.0.1/channel/42", "https://t.me/c/0/42",
            "https://t.me/c/123/42?redirect=http://evil", "https://t.me/a/1/anything",
            "https://t.me/channel/44#redirect", "javascript:alert(1)", None,
        ):
            with self.subTest(link=link), self.assertRaises(ValueError):
                tg.parse_link(link)

    def test_select_seven_multipart_telegram_documents(self):
        parts=[message(i, msgid=i+2000) for i in range(1,8)]
        first=parts[0]
        name, sha, ordered, size=tg.select_parts(parts[::-1], first)
        self.assertEqual(name, NAME)
        self.assertEqual(sha, SHA)
        self.assertEqual([m.file.name for m in ordered],
                         [NAME+".part-"+str(i).zfill(4) for i in range(7)])
        self.assertEqual(size, 315000000)

    def test_telegram_bot_caption_integrity_and_missing_parts(self):
        parts=[message(i) for i in range(1,8)]
        with self.assertRaisesRegex(ValueError, "Missing Telegram parts"):
            tg.select_parts(parts[:-1], parts[0])
        parts[2].raw_text=parts[2].raw_text.replace(SHA, "b"*64)
        with self.assertRaisesRegex(ValueError, "Missing Telegram parts"):
            tg.select_parts(parts, parts[0])
        parts[2]=message(3, filename="../../etc/passwd")
        with self.assertRaisesRegex(ValueError, "Missing Telegram parts"):
            tg.select_parts(parts, parts[0])
        with self.assertRaisesRegex(ValueError, "PART 1"):
            tg.select_parts(parts, parts[1])

    def test_telegram_single_part_and_three_digit_format(self):
        single=message(1, total=1)
        self.assertEqual(len(tg.select_parts([], single)[2]), 1)
        parts=[message(i, total=2, suffix=3) for i in range(1,3)]
        self.assertEqual(len(tg.select_parts(parts, parts[0])[2]), 2)

    def test_progress_file_and_job_ownership(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(tg, "RUN", Path(folder)):
            job="a"*12
            tg.write_job(job, "queued", "queued", 0)
            running=tg.write_job(job, "running", "downloading", 70,
                                 completedParts=4, totalParts=7)
            self.assertEqual(running["completedParts"], 4)
            self.assertEqual(tg.current_job()["percent"], 70)
            with self.assertRaisesRegex(ValueError, "Another job"):
                tg.write_job("b"*12, "completed", "completed", 100)
            after=tg.write_job("b"*12, "queued", "queued", 0)
            self.assertEqual(after["totalParts"], 0)
            self.assertEqual((Path(folder)/"telegram-download-status.json").stat().st_mode & 0o777, 0o600)

    def test_security_contract_and_kept_restore_confirmation(self):
        source=Path("deploy/wgdashbackup_telegram.py").read_text()
        self.assertIn("from telethon import TelegramClient", source)
        self.assertIn("Telegram USER", Path("docs/INSTALL.md").read_text())
        agent=Path("deploy/wgdashbackup-panel-agent.py").read_text()
        self.assertIn('if operation == "download_telegram":', agent)
        self.assertIn('"systemd-run", "--collect", "--no-block"', agent)
        self.assertIn('if operation in ("verify", "restore"):', agent)
        self.assertIn('payload.get("confirmation") != "RESTORE " + name', agent)
        routes=Path("src/dashboard.py").read_text()
        self.assertIn('"download_telegram"', routes)
        self.assertIn('if not _backup_ui_authorized():', routes)
        compose=Path("deploy/compose.yaml").read_text()
        self.assertNotIn(":/root:",compose)
        self.assertNotIn("/var/run/docker.sock",compose)


if __name__=="__main__":
    unittest.main()
