import json
import os
from pathlib import Path
import tempfile
import unittest

from atlas.asset_settings import SettingsStore


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.home() / ".hermes/cache/scratch")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.store = SettingsStore(self.root)

    def test_secrets_never_returned_and_blank_preserves(self):
        public = self.store.save({"baseUrl": "https://embed.example/v1", "model": "test-model", "apiKey": "fixture-secret"})
        self.assertTrue(public["apiKeyConfigured"])
        self.assertNotIn("fixture-secret", json.dumps(public))
        self.assertNotIn("fixture-secret", (self.root / "settings.json").read_text())
        self.assertEqual(self.store.private()["apiKey"], "fixture-secret")
        self.store.save({"apiKey": "", "model": "second-model"})
        self.assertEqual(self.store.private()["apiKey"], "fixture-secret")
        self.assertEqual(os.stat(self.root / "embedding-key").st_mode & 0o777, 0o600)
        self.store.save({"clearApiKey": True})
        self.assertFalse(self.store.public()["apiKeyConfigured"])

    def test_invalid_configuration_does_not_replace_settings(self):
        self.store.save({"baseUrl": "https://embed.example/v1", "model": "good"})
        invalid = [
            {"baseUrl": "http://remote.example/v1"},
            {"baseUrl": "https://user:secret@remote.example/v1"},
            {"baseUrl": "https://remote.example/v1?key=secret"},
            {"dimensions": -1}, {"dimensions": True}, {"batchSize": 0},
            {"categories": ["made-up"]}, {"model": ["bad"]},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.save(value)
        self.assertEqual(self.store.public()["model"], "good")

    def test_cloud_scope_excludes_private_high_volume_categories_by_default(self):
        data = self.store.public()
        self.assertFalse(data["apiKeyConfigured"])
        self.assertNotIn("session", data["categories"])
        self.assertNotIn("log", data["categories"])
        self.assertNotIn("config", data["categories"])
        self.assertEqual(self.store.save({"dimensions": "1024"})["dimensions"], 1024)
        self.assertIsNone(self.store.save({"dimensions": ""})["dimensions"])

    def test_authorization_snapshot_is_revoked_by_changes_even_if_reverted(self):
        self.store.save({"baseUrl": "https://embed.example/v1", "model": "fixture",
                         "apiKey": "fixture-secret", "categories": ["memory"]})
        approved = self.store.authorized()
        approved["_authorize"]()
        self.store.save({"categories": ["memory", "session"]})
        self.store.save({"categories": ["memory"]})
        with self.assertRaisesRegex(ValueError, "授权"):
            approved["_authorize"]()
        self.assertEqual(approved["categories"], ["memory"])
        self.assertNotIn("_authorize", self.store.public())

    def test_authorization_detects_key_changes_from_another_store(self):
        self.store.save({"apiKey": "fixture-secret"})
        approved = self.store.authorized()
        SettingsStore(self.root).save({"clearApiKey": True})
        with self.assertRaisesRegex(ValueError, "授权"):
            approved["_authorize"]()


if __name__ == "__main__":
    unittest.main()
