#!/usr/bin/env python3
"""No-live-Telegram regression for grouped backups and Bot API recovery."""
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path("deploy").resolve()))
import wgdashbackup_botfiles as bot
import wgdashbackup_telegram as telegram
import wgdashbackup_import as importer

NAME="wgdashboard-20261010-030000.tar.gz"


class BotCatalogTests(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root=Path(self.dir.name)
        for d in ("catalog","stage","download","status"):
            (self.root/d).mkdir()
        for obj, name, val in (
            (bot,"CATALOG",self.root/"catalog"),
            (importer,"UPLOADS",self.root/"download"),
            (telegram,"RUN",self.root/"status"),
        ):
            p=patch.object(obj,name,val)
            p.start()
            self.addCleanup(p.stop)

    def test_telegram_album_sent_in_groups_and_catalog_file_ids_saved(self):
        stage=self.root/"stage"
        contents=[b"abcdefghi",b"123456789",b"abcd"]
        for i, content in enumerate(contents):
            (stage/(NAME+".part-"+str(i).zfill(4))).write_bytes(content)
        calls=[]
        def fake_api(token, method, fields, attachments=()):
            calls.append((method,fields,attachments))
            if method=="sendMessage":
                return {"message_id":321}
            if method=="sendMediaGroup":
                assert len(attachments)==3
                media=__import__("json").loads(fields["media"])
                self.assertEqual(len(media),3)
                self.assertTrue(all("PART " in item["caption"] for item in media))
                return [{"document":{"file_id":"AAAbotfileID"+str(i),
                                     "file_size":p.stat().st_size}}
                        for i,p in enumerate(attachments)]
            raise AssertionError(method)
        with patch.object(bot,"PART_BYTES",10), patch.object(bot,"credentials",
                return_value=("123:abc","-123")), patch.object(bot,"_bot_call",
                side_effect=fake_api):
            data=bot.send_archive(NAME,stage)
        self.assertEqual(len(data["files"]),3)
        self.assertEqual(data["sha256"],hashlib.sha256(b"".join(contents)).hexdigest())
        self.assertEqual(len(bot.available()),1)
        self.assertEqual(bot.available()[0]["parts"],3)
        self.assertNotIn("file_id",str(bot.available()))
        self.assertEqual(bot.load(NAME)["files"][1]["bytes"],9)
        self.assertEqual([x[0] for x in calls],["sendMessage","sendMediaGroup","sendMessage"])
        self.assertEqual((bot.CATALOG/(NAME+".json")).stat().st_mode&0o777,0o600)

    def test_small_backup_single_document_keeps_download_catalog(self):
        path=self.root/"stage"/NAME
        path.write_bytes(b"small-backup")
        with patch.object(importer,"ARCHIVES",self.root/"stage"), \
             patch.object(bot,"credentials",return_value=("123:abc","@test")):
            def fake_api(token,method,fields,attachments=()):
                if method=="sendMessage":
                    return {"message_id":123}
                if method=="sendDocument":
                    self.assertIn("document",fields)
                    self.assertIn(str(path),fields["document"])
                    return {"document":{"file_id":"AAAA123456bot",
                                        "file_size":path.stat().st_size}}
                raise AssertionError(method)
            with patch.object(bot,"_bot_call",side_effect=fake_api):
                data=bot.send_archive(NAME,self.root/"download")
        self.assertEqual(len(data["files"]),1)
        self.assertEqual(data["files"][0]["name"],NAME)

    def test_telegram_group_validation_and_no_arbitrary_ids_from_browser(self):
        name=NAME
        payload={"name":name,"bytes":10,"sha256":"a"*64,
                 "files":[{"name":name,"bytes":10,"file_id":"AAAbotfile123"}]}
        self.assertTrue(bot._valid_manifest(payload))
        self.assertFalse(bot._valid_manifest({**payload,"files":[
            {"name":"../../etc/shadow","bytes":10,"file_id":"AAAbotfile123"}]}))
        self.assertFalse(bot._valid_manifest({**payload,"files":[
            {"name":name,"bytes":10,"file_id":"bad!!"}]}))
        with self.assertRaises(ValueError):
            bot.catalog_path("../../etc/passwd")
        with self.assertRaises(ValueError):
            bot.load(NAME)

    def test_bot_download_validates_sha_and_imports_not_restores(self):
        content=[b"a"*100,b"b"*200]
        stage=self.root/"catalog"
        manifest={"name":NAME,"bytes":300,
                  "sha256":hashlib.sha256(b"".join(content)).hexdigest(),
                  "files":[{"name":NAME+".part-"+str(i).zfill(4),
                            "bytes":len(x),"file_id":"AAAbotfileID"+str(i)}
                           for i,x in enumerate(content)]}
        bot._atomic_json(bot.catalog_path(NAME),manifest)
        import_calls=[]
        def fake_download(token, file_id, target, expected_bytes, update):
            index=int(file_id[-1])
            target.write_bytes(content[index])
            update(len(content[index]))
        def fake_import(root,name,execute):
            import_calls.append((root,name))
            return {"name":"wgdashboard-imported-20261010-032222-abcd1234.tar.gz"}
        with patch.object(bot,"credentials",return_value=("123:abc","@test")), \
             patch.object(bot,"_download",side_effect=fake_download), \
             patch.object(importer,"import_backup",side_effect=fake_import):
            bot.download_archive("a"*12,NAME)
        self.assertEqual(import_calls[0][1],NAME)
        self.assertEqual(telegram.current_job()["state"],"completed")
        self.assertEqual(telegram.current_job()["completedParts"],2)
        self.assertEqual(len(list((self.root/"download").iterdir())),0)
        self.assertNotIn("restore",Path("deploy/wgdashbackup_botfiles.py").read_text().split("def download_archive",1)[1].split("def run_job",1)[0])

    def test_corrupted_parts_cannot_be_imported(self):
        content=[b"a"*10,b"b"*10]
        manifest={"name":NAME,"bytes":20,"sha256":"f"*64,
                  "files":[{"name":NAME+".part-"+str(i).zfill(4),
                            "bytes":10,"file_id":"AAAbotfileID"+str(i)}
                           for i in range(2)]}
        bot._atomic_json(bot.catalog_path(NAME),manifest)
        def fake_down(token,fid,target,expected,update):
            target.write_bytes(content[int(fid[-1])])
            update(expected)
        with patch.object(bot,"credentials",return_value=("123:abc","@test")), \
             patch.object(bot,"_download",side_effect=fake_down), \
             patch.object(importer,"import_backup") as imported:
            with self.assertRaisesRegex(ValueError,"SHA256"):
                bot.download_archive("a"*12,NAME)
            imported.assert_not_called()

    def test_host_auth_and_limited_part_size_contract(self):
        agent=Path("deploy/wgdashbackup-panel-agent.py").read_text()
        self.assertIn('if operation == "download_bot_backup":',agent)
        self.assertIn('botfiles.load(name)',agent)
        self.assertIn("botfiles.available()",agent)
        dashboard=Path("src/dashboard.py").read_text()
        self.assertIn('"download_bot_backup"',dashboard)
        self.assertIn('if not _backup_ui_authorized():',dashboard)
        shell=Path("deploy/wgdashbackup.sh").read_text()
        self.assertIn("split -b 18000000",shell)
        self.assertIn("wgdashbackup_botfiles.py send",shell)
        self.assertNotIn("split -b 45000000",shell)
        self.assertIn("MAX_FILES = 64",Path("deploy/wgdashbackup_import.py").read_text())
        installer=Path("install.sh").read_text()
        self.assertIn("wgdashbackup_botfiles.py",installer)

if __name__=="__main__":
    unittest.main()
