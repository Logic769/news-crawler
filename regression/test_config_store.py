"""news_api.config_store 的离线单元测试（不联网）。"""
from __future__ import annotations

import errno
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from news_api import config_store

BASE = {
    "workers": 3,
    "timezone": "Asia/Shanghai",
    "sources": [
        {
            "name": "existing",
            "list_url": "https://example.com/list",
            "item_xpath": "//li",
            "pagination": {"mode": "next"},
            "api": {"url": "https://example.com/api", "headers": {"Authorization": "secret"}},
        }
    ],
}


class ConfigStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "sources.local.json"
        self.path.write_text(json.dumps(BASE, ensure_ascii=False), encoding="utf-8")
        self.backups = self.root / "backups"
        self.env = mock.patch.dict(os.environ, {"NEWS_BACKUP_DIR": str(self.backups)})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def test_load_config_skips_disabled_sources(self) -> None:
        """管理台停用后，抓取器加载配置时不能再把该站点带进队列。"""
        import news_crawler

        data = json.loads(json.dumps(BASE))
        data["sources"][0]["enabled"] = False
        data["sources"].append(
            {
                "name": "active",
                "list_url": "https://example.com/active",
                "item_xpath": "//li",
            }
        )
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        config = news_crawler.load_config(str(self.path))
        self.assertEqual([site["name"] for site in config["sources"]], ["active"])

    def test_public_view_never_leaks_api_headers(self) -> None:
        sites = config_store.list_sites(self.path)
        self.assertEqual(len(sites), 1)
        self.assertTrue(sites[0]["has_api"])
        self.assertNotIn("api", sites[0])
        self.assertNotIn("headers", json.dumps(sites, ensure_ascii=False))

    def test_create_update_delete_keep_unknown_keys_and_backup(self) -> None:
        created = config_store.create_site(
            self.path,
            {
                "name": "new-site",
                "list_url": "https://new.example.com/list",
                "item_xpath": "//article",
                "title_xpath": ".//h2",
                "url_xpath": ".//a/@href",
                "enabled": False,
            },
        )
        self.assertEqual(created["name"], "new-site")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["workers"], 3)
        self.assertEqual(data["sources"][0]["pagination"], {"mode": "next"})
        self.assertEqual(data["sources"][0]["api"]["headers"]["Authorization"], "secret")
        self.assertTrue(list(self.backups.glob("sources_local_*.json")))

        updated = config_store.update_site(self.path, "new-site", {"enabled": True})
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertTrue(updated["enabled"])
        self.assertEqual(updated["title_xpath"], ".//h2")

        self.assertTrue(config_store.delete_site(self.path, "new-site"))
        self.assertFalse(config_store.delete_site(self.path, "new-site"))
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual([site["name"] for site in data["sources"]], ["existing"])

    def test_write_falls_back_to_in_place_when_bind_mount_is_busy(self) -> None:
        # Docker 单文件 bind mount 上 os.replace 会 EBUSY；必须原地写成功，
        # 否则管理台改站点在容器里永远 500。
        with mock.patch(
            "news_api.config_store.os.replace",
            side_effect=OSError(errno.EBUSY, "Device or resource busy"),
        ):
            updated = config_store.update_site(self.path, "existing", {"enabled": False})
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertFalse(updated["enabled"])
        self.assertFalse((self.root / "sources.local.json.tmp").exists())
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertFalse(data["sources"][0]["enabled"])
        self.assertTrue(list(self.backups.glob("sources_local_*.json")))

    def test_duplicate_name_and_unknown_update(self) -> None:
        with self.assertRaisesRegex(ValueError, "站点已存在"):
            config_store.create_site(
                self.path, {"name": "existing", "list_url": "https://x.example.com"}
            )
        self.assertIsNone(config_store.update_site(self.path, "missing", {"enabled": False}))


if __name__ == "__main__":
    unittest.main()
