"""Config and JSON-store tests, including the multipart upload parser."""

from __future__ import annotations

import os
import tempfile
import unittest

from ufitools.api.media_api import parse_multipart
from ufitools.config import Config
from ufitools.store import PluginStore, TaskStore, ThemeStore, normalize_task
from ufitools import auth


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.config = Config(data_dir=self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_defaults(self):
        self.assertEqual(self.config.token_hash, auth.sha256_hex("admin"))
        self.assertTrue(self.config.token_enabled)
        self.assertTrue(self.config.is_weak_token)
        self.assertEqual(self.config.get("port"), 2333)
        self.assertEqual(self.config.get("mobile_data_unit"), "e5-mobile-data.service")
        self.assertEqual(self.config.get("hotspot_unit"), "e5-hotspot.service")
        self.assertEqual(self.config.get("at_socket"), "/run/e5-atd.sock")
        # No vendor vocabulary survives in the defaults.
        for key in ("gateway_ip", "ADMIN_PWD", "device_mode", "web_server_cookie"):
            self.assertNotIn(key, self.config.data, key)

    def test_persistence(self):
        self.config.set("nickname", "客厅路由")
        reloaded = Config(data_dir=self._tmp.name)
        self.assertEqual(reloaded.get("nickname"), "客厅路由")

    def test_plaintext_token_is_hashed_on_load(self):
        path = os.path.join(self._tmp.name, "config.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('{"login_token": "plaintext-token"}')
        config = Config(data_dir=self._tmp.name)
        self.assertEqual(config.token_hash, auth.sha256_hex("plaintext-token"))

    def test_corrupt_config_falls_back_to_defaults(self):
        with open(os.path.join(self._tmp.name, "config.json"), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        config = Config(data_dir=self._tmp.name)
        self.assertEqual(config.get("port"), 2333)
        self.assertEqual(config.token_hash, auth.sha256_hex("admin"))

    def test_set_token_clears_weak_flag(self):
        self.config.set_token("abcd1234")
        self.assertFalse(self.config.is_weak_token)

    def test_at_poll_interval_is_clamped(self):
        self.config.set("at_poll_interval", -5)
        self.assertEqual(self.config.at_poll_interval, 0.0)
        self.config.set("at_poll_interval", "30")
        self.assertEqual(self.config.at_poll_interval, 30.0)

    def test_secrets_are_not_dumped(self):
        self.config.set("kano_smtp_password", "hunter2")
        dumped = self.config.as_public_dict()
        self.assertNotIn("login_token", dumped)
        self.assertNotIn("kano_smtp_password", dumped)

    def test_device_uuid_is_created(self):
        self.assertTrue(self.config.get("device_uuid"))

    def test_external_edit_is_picked_up(self):
        """``ufi-tools set-token`` must reach a running service."""
        self.config.RELOAD_INTERVAL = 0.0
        other = Config(data_dir=self._tmp.name)
        other.set_token("linux2026ab")
        # bump the mtime so the change is unambiguous on coarse filesystems
        future = os.stat(other.path).st_mtime + 5
        os.utime(other.path, (future, future))
        self.assertEqual(self.config.token_hash, auth.sha256_hex("linux2026ab"))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def _path(self, name):
        return os.path.join(self._tmp.name, name)

    def test_theme_merges_defaults(self):
        store = ThemeStore(self._path("theme.json"))
        store.update({"themeColor": "120"})
        merged = store.merged()
        self.assertEqual(merged["themeColor"], "120")
        self.assertEqual(merged["textColor"], "rgba(255, 255, 255, 1)")

    def test_theme_ignores_unknown_keys(self):
        store = ThemeStore(self._path("theme.json"))
        store.update({"nonsense": "1", "brightPer": "50"})
        self.assertNotIn("nonsense", store.merged())
        self.assertEqual(store.merged()["brightPer"], "50")

    def test_plugin_round_trip(self):
        store = PluginStore(self._path("plugins.json"))
        store.set_text("<script>x</script>")
        self.assertEqual(PluginStore(self._path("plugins.json")).text, "<script>x</script>")

    def test_task_store_add_replaces_by_id(self):
        store = TaskStore(self._path("tasks.json"))
        task = normalize_task({"id": "a", "time": "03:00:00", "action": {"goformId": "REBOOT_DEVICE"}})
        store.add(task)
        self.assertEqual(len(store.tasks()), 1)
        self.assertEqual(store.tasks()[0]["time"], "03:00")
        store.add(dict(task, time="04:00"))
        self.assertEqual(len(store.tasks()), 1)
        self.assertEqual(store.tasks()[0]["time"], "04:00")
        self.assertTrue(store.remove("a"))
        self.assertFalse(store.remove("a"))

    def test_normalize_task_validation(self):
        with self.assertRaises(ValueError):
            normalize_task({"time": "03:00", "action": {"goformId": "X"}})
        with self.assertRaises(ValueError):
            normalize_task({"id": "a", "time": "25:00", "action": {"goformId": "X"}})
        with self.assertRaises(ValueError):
            normalize_task({"id": "a", "time": "03:00", "action": "nope"})

    def test_normalize_task_stringifies_action_values(self):
        task = normalize_task({"id": "a", "time": "03:00",
                               "action": {"goformId": "SET_SIM_SLOT", "sim_slot": 1}})
        self.assertEqual(task["actionMap"]["sim_slot"], "1")


class MultipartTests(unittest.TestCase):
    BODY = (
        b"--X\r\n"
        b'Content-Disposition: form-data; name="file"; filename="bg.webp"\r\n'
        b"Content-Type: image/webp\r\n\r\n"
        b"BINARYDATA\r\n"
        b"--X--\r\n"
    )

    def test_parses_file_part(self):
        parts = parse_multipart(self.BODY, 'multipart/form-data; boundary="X"')
        self.assertEqual(len(parts), 1)
        name, filename, payload = parts[0]
        self.assertEqual(name, "file")
        self.assertEqual(filename, "bg.webp")
        self.assertEqual(payload, b"BINARYDATA")

    def test_missing_boundary_raises(self):
        from ufitools.httpd import ApiError

        with self.assertRaises(ApiError):
            parse_multipart(self.BODY, "multipart/form-data")


if __name__ == "__main__":
    unittest.main()
