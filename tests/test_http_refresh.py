"""Real HTTP + SQLite refresh tests; all files and scans stay in scratch."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from atlas import catalog, scan, serve
from atlas.asset_service import AssetService


class HttpRefreshTests(unittest.TestCase):
    def setUp(self):
        scratch = Path.home() / ".hermes/cache/scratch"
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas-http-refresh-", dir=str(scratch))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.old = self.repo / "AGENTS.md"
        self.old.write_text("# Fixture\noldtoken\n", encoding="utf-8")
        self.atlas_path = self.root / "atlas.json"
        for target, value in (
            ("pathlib.Path.home", self.root),
            ("atlas.scan.HOME", str(self.root)),
            ("atlas.scan.ROOTS", [str(self.repo)]),

            ("atlas.serve.HOME", str(self.root)),
            ("atlas.serve.ATLAS_JSON", str(self.atlas_path)),
        ):
            handle = patch(target, return_value=value) if target == "pathlib.Path.home" else patch(target, value)
            handle.start()
            self.addCleanup(handle.stop)
        self.fixture_scan()
        self.service = AssetService(
            self.root, serve.load_index,
            discover_fn=lambda **kw: catalog.discover(home=self.root, **kw),
            reader=catalog.read_text,
        )
        self.service.start()
        self.wait_job()
        scanner = patch.object(serve.subprocess, "run", side_effect=self.fixture_scan)
        self.scanner = scanner.start()
        self.addCleanup(scanner.stop)
        embedding = patch.object(self.service.index, "embed_pending",
                                 side_effect=AssertionError("Unexpected cloud work"))
        self.embedding = embedding.start()
        self.addCleanup(embedding.stop)
        self.server = serve.ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
        self.server.assets = self.service
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = "http://127.0.0.1:%s" % self.server.server_port

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.wait_job()

    def wait_job(self):
        if self.service.worker is not None:
            self.service.worker.join(5)
            self.assertFalse(self.service.worker.is_alive(), "fixture index worker did not stop")
        self.assertIsNone(self.service.status()["job"].get("error"))

    def fixture_scan(self, *args, **kwargs):
        # Exercise the real scanner, but only with explicit fixture roots.

        data = scan.scan()
        self.atlas_path.write_text(json.dumps(data), encoding="utf-8")
        return subprocess.CompletedProcess(args[0] if args else [], 0, "fixture scanned\n", "")

    def call(self, path, body=None, headers=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        try:
            with urlopen(Request(self.url + path, data=data, headers=request_headers), timeout=5) as reply:
                return reply.status, json.load(reply)
        except HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)

    def assert_rescan_updates_snapshots(self, endpoint, expected_status):
        added = self.repo / "nested" / "AGENTS.md"
        added.parent.mkdir()
        added.write_text("# Fixture\nnewtoken\n", encoding="utf-8")
        self.old.unlink()
        before = (added.read_bytes(), added.stat().st_mtime_ns, added.stat().st_mode)
        status, response = self.call(endpoint, {})
        self.assertEqual(status, expected_status, response)
        self.assertTrue(response["ok"])
        self.assertEqual(self.scanner.call_count, 1)
        self.assertEqual(response["data"]["stdout"], "fixture scanned\n")
        self.assertIn("job", response["data"])
        self.assertIs(response["data"]["job"]["embeddings"], False)
        self.wait_job()
        tree_status, tree = self.call("/api/tree")
        assets_status, assets = self.call("/api/assets")
        self.assertEqual((tree_status, assets_status), (200, 200))
        self.assertEqual([f["path"] for f in tree["data"]["files"]], [str(added)])
        self.assertEqual([f["path"] for f in assets["data"]["files"]], [str(added)])
        self.assertEqual(tree["data"], json.loads(self.atlas_path.read_text()))
        self.assertEqual(assets["data"], json.loads(self.service.catalog_path.read_text()))
        self.assertEqual(self.call("/api/search?q=newtoken")[1]["data"]["total"], 1)
        self.assertEqual(self.call("/api/search?q=oldtoken")[1]["data"]["total"], 0)
        self.assertEqual((added.read_bytes(), added.stat().st_mtime_ns, added.stat().st_mode), before)
        self.assertFalse(self.old.exists())
        self.embedding.assert_not_called()

    def test_rescans_reject_every_nonempty_payload_without_side_effects(self):
        before = (self.atlas_path.read_bytes(), self.service.catalog_path.read_bytes(),
                  self.old.read_bytes(), self.service.status())
        for endpoint in ("/api/rescan", "/api/assets/rescan"):
            for payload in ({"embeddings": True, "confirmCloud": True},
                            {"embeddings": False}, {"confirmCloud": False},
                            {"maxChunks": 1}, {"unexpected": None}):
                with self.subTest(endpoint=endpoint, payload=payload):
                    status, result = self.call(endpoint, payload)
                    self.assertEqual(status, 400, result)
                    self.assertFalse(result["ok"])
        self.scanner.assert_not_called()
        self.embedding.assert_not_called()
        self.assertEqual((self.atlas_path.read_bytes(), self.service.catalog_path.read_bytes(),
                          self.old.read_bytes(), self.service.status()), before)

    def test_running_cloud_index_rejects_rescans_before_scanner(self):
        self.service.settings.save({"baseUrl": "https://fixture.example/v1", "model": "fixture",
                                    "apiKey": "synthetic-only", "categories": ["instruction"]})
        entered, release = threading.Event(), threading.Event()
        authorizations = []

        def blocked_embedding(settings, **kwargs):
            settings["_authorize"]()
            authorizations.append("before")
            entered.set()
            if not release.wait(5):
                raise AssertionError("fixture cloud boundary was not released")
            settings["_authorize"]()
            authorizations.append("after")
            return {"embedded": 0}

        self.embedding.side_effect = blocked_embedding
        try:
            status, result = self.call("/api/search/index", {"embeddings": True, "confirmCloud": True})
            self.assertEqual(status, 202, result)
            self.assertTrue(entered.wait(3))
            before = (self.atlas_path.read_bytes(), self.service.catalog_path.read_bytes(),
                      self.service.status(), self.service.settings.public())
            for endpoint in ("/api/rescan", "/api/assets/rescan", "/api/search/index"):
                with self.subTest(endpoint=endpoint):
                    status, result = self.call(endpoint, {})
                    self.assertEqual(status, 409, result)
                    self.assertFalse(result["ok"])
            self.scanner.assert_not_called()
            self.assertFalse(self.service.refresh_requested)
            self.assertEqual((self.atlas_path.read_bytes(), self.service.catalog_path.read_bytes(),
                              self.service.status(), self.service.settings.public()), before)
        finally:
            release.set()
            self.wait_job()
        self.assertEqual(authorizations, ["before", "after"])
        self.embedding.assert_called_once()
        self.assertIs(self.service.status()["job"]["embeddings"], True)

    def test_scanning_excludes_both_rescan_routes_and_search_index(self):
        for first in ("/api/rescan", "/api/assets/rescan"):
            for second in ("/api/rescan", "/api/assets/rescan", "/api/search/index"):
                with self.subTest(first=first, second=second):
                    entered, release = threading.Event(), threading.Event()

                    def blocked_scan(*args, **kwargs):
                        if not entered.is_set():
                            entered.set()
                            if not release.wait(5):
                                raise AssertionError("fixture scanner was not released")
                        return self.fixture_scan(*args, **kwargs)

                    self.scanner.reset_mock(side_effect=True)
                    self.scanner.side_effect = blocked_scan
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        initial = pool.submit(self.call, first, {})
                        try:
                            self.assertTrue(entered.wait(3))
                            before = (self.atlas_path.read_bytes(), self.service.status())
                            status, result = self.call(second, {})
                            self.assertEqual(status, 409, result)
                            self.assertFalse(result["ok"])
                            self.assertEqual(self.scanner.call_count, 1)
                            self.assertEqual((self.atlas_path.read_bytes(), self.service.status()), before)
                        finally:
                            release.set()
                            initial_result = initial.result(timeout=5)
                            self.wait_job()
                    self.assertEqual(initial_result[0], 200 if first == "/api/rescan" else 202,
                                     initial_result)
        self.embedding.assert_not_called()

    def test_save_during_scan_keeps_version_safety_and_queues_fresh_local_sync(self):
        scan_entered, scan_release = threading.Event(), threading.Event()
        sync_entered, sync_release = threading.Event(), threading.Event()
        discover = self.service.discover
        instruction_versions = []

        def blocked_scan(*args, **kwargs):
            scan_entered.set()
            if not scan_release.wait(5):
                raise AssertionError("fixture scanner was not released")
            return self.fixture_scan(*args, **kwargs)

        def blocked_discover(**kwargs):
            instruction_versions.append(kwargs["instruction_files"][0]["sha"])
            sync_entered.set()
            if not sync_release.wait(5):
                raise AssertionError("fixture discovery was not released")
            return discover(**kwargs)

        self.scanner.side_effect = blocked_scan
        self.service.discover = blocked_discover
        original = self.old.read_bytes()
        base = self.service.file_store.read(str(self.old))["sha256"]
        with ThreadPoolExecutor(max_workers=1) as pool:
            initial = pool.submit(self.call, "/api/rescan", {})
            try:
                self.assertTrue(scan_entered.wait(3))
                payload = {"path": str(self.old), "content": "# Fixture\nsavedtoken\n", "baseVersion": base}
                status, saved = self.call("/api/save", payload)
                self.assertEqual(status, 200, saved)
                self.assertTrue(sync_entered.wait(3))
                after_save = (self.old.read_bytes(), self.old.stat().st_mtime_ns)
                self.assertEqual(self.call("/api/save", dict(payload, content="stale"))[0], 409)
                scan_release.set()
                status, result = initial.result(timeout=5)
                self.assertEqual(status, 200, result)
                self.assertIs(result["data"]["job"]["embeddings"], False)
                self.assertTrue(result["data"]["job"]["refreshQueued"])
                self.assertEqual((self.old.read_bytes(), self.old.stat().st_mtime_ns), after_save)
                self.assertEqual(Path(saved["data"]["backup"]).read_bytes(), original)
            finally:
                scan_release.set()
                sync_release.set()
                initial.result(timeout=5)
                self.wait_job()
        self.assertEqual(len(instruction_versions), 2)
        self.assertNotEqual(instruction_versions[0], instruction_versions[1])
        disk = json.loads(self.atlas_path.read_text())
        self.assertEqual(self.service.snapshot()["files"][0]["sha"], disk["files"][0]["sha"])
        self.assertEqual(self.call("/api/search?q=savedtoken")[1]["data"]["total"], 1)
        self.embedding.assert_not_called()

    def test_scanner_failures_never_refresh_and_release_admission_lock(self):
        failures = (
            subprocess.CompletedProcess(["fixture"], 7, "", "fixture scan failed"),
            subprocess.TimeoutExpired(["fixture"], 300),
            OSError("fixture scanner unavailable"),
        )
        for endpoint in ("/api/rescan", "/api/assets/rescan"):
            for failure in failures:
                with self.subTest(endpoint=endpoint, failure=type(failure).__name__):
                    before = (self.atlas_path.read_bytes(), self.service.catalog_path.read_bytes(),
                              self.old.read_bytes(), self.service.status())
                    if isinstance(failure, Exception):
                        self.scanner.side_effect = failure
                    else:
                        self.scanner.side_effect = lambda *args, **kwargs: failure
                    with patch.object(self.service, "refresh", wraps=self.service.refresh) as refresh, \
                            patch.object(self.service, "start", wraps=self.service.start) as start:
                        status, result = self.call(endpoint, {})
                        self.assertEqual(status, 500, result)
                        self.assertFalse(result["ok"])
                        self.assertNotIn("data", result)
                        refresh.assert_not_called()
                        start.assert_not_called()
                    self.assertEqual((self.atlas_path.read_bytes(), self.service.catalog_path.read_bytes(),
                                      self.old.read_bytes(), self.service.status()), before)
                    self.scanner.side_effect = self.fixture_scan
                    status, result = self.call(endpoint, {})
                    self.assertEqual(status, 200 if endpoint == "/api/rescan" else 202, result)
                    self.wait_job()
        self.embedding.assert_not_called()

    def test_rescans_retain_json_and_same_origin_request_guards(self):
        before = (self.atlas_path.read_bytes(), self.service.catalog_path.read_bytes(),
                  self.old.read_bytes(), self.service.status())
        for endpoint in ("/api/rescan", "/api/assets/rescan"):
            for raw, headers, expected in (
                (b"{}", {"Content-Type": "text/plain"}, 415),
                (b"{", {}, 400),
                (b"[]", {}, 400),
                (b"null", {}, 400),
                (b"\xff", {}, 400),
                (b"{}", {"Origin": "https://evil.example"}, 403),
                (b"{}", {"Host": "evil.example"}, 403),
                (b"{}", {"Sec-Fetch-Site": "cross-site"}, 403),
                (b"{}", {"Content-Length": str(4 * 1024 * 1024 + 1)}, 413),
            ):
                with self.subTest(endpoint=endpoint, headers=headers, raw=raw):
                    status, result = self.call(endpoint, raw=raw, headers=headers)
                    self.assertEqual(status, expected, result)
                    self.assertFalse(result["ok"])
        self.scanner.assert_not_called()
        self.embedding.assert_not_called()
        self.assertEqual((self.atlas_path.read_bytes(), self.service.catalog_path.read_bytes(),
                          self.old.read_bytes(), self.service.status()), before)
        # Valid same-origin requests and zero-length JSON remain accepted.
        for endpoint in ("/api/rescan", "/api/assets/rescan"):
            status, result = self.call(endpoint, raw=b"", headers={"Origin": self.url})
            self.assertEqual(status, 200 if endpoint == "/api/rescan" else 202, result)
            self.wait_job()

    def test_index_cloud_requires_exact_explicit_confirmation_on_its_own_route(self):
        self.service.settings.save({"baseUrl": "https://fixture.example/v1", "model": "fixture",
                                    "apiKey": "synthetic-only", "categories": ["instruction"]})
        before = self.service.status()
        for confirmation in (None, False, 1, "true"):
            with self.subTest(confirmation=confirmation):
                status, result = self.call("/api/search/index", {
                    "embeddings": True, "confirmCloud": confirmation})
                self.assertEqual(status, 400, result)
                self.assertFalse(result["ok"])
                self.assertEqual(self.service.status(), before)
        for endpoint in ("/api/rescan", "/api/assets/rescan"):
            self.assertEqual(self.call(endpoint, {"embeddings": True, "confirmCloud": True})[0], 400)
        self.embedding.assert_not_called()
        self.scanner.assert_not_called()
        status, result = self.call("/api/search/index", {"confirmCloud": True})
        self.assertEqual(status, 202, result)
        self.assertIs(result["data"]["embeddings"], False)
        self.wait_job()
        self.embedding.assert_not_called()
        # Only this explicitly confirmed index route may reach the test boundary.
        self.embedding.side_effect = lambda settings, **kwargs: {"embedded": 0}
        status, result = self.call("/api/search/index", {
            "embeddings": True, "confirmCloud": True, "maxChunks": 3})
        self.assertEqual(status, 202, result)
        self.assertIs(result["data"]["embeddings"], True)
        self.wait_job()
        self.embedding.assert_called_once()
        self.assertEqual(self.embedding.call_args.kwargs["max_chunks"], 3)
        self.scanner.assert_not_called()

    def test_index_admission_excludes_scan_before_worker_has_started(self):
        entered, release = threading.Event(), threading.Event()
        start = self.service.start

        def blocked_start(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("fixture index admission was not released")
            return start(*args, **kwargs)

        with patch.object(self.service, "start", side_effect=blocked_start):
            with ThreadPoolExecutor(max_workers=1) as pool:
                initial = pool.submit(self.call, "/api/search/index", {})
                try:
                    self.assertTrue(entered.wait(3))
                    before = self.atlas_path.read_bytes()
                    for endpoint in ("/api/rescan", "/api/assets/rescan", "/api/search/index"):
                        status, result = self.call(endpoint, {})
                        self.assertEqual(status, 409, result)
                    self.scanner.assert_not_called()
                    self.assertEqual(self.atlas_path.read_bytes(), before)
                finally:
                    release.set()
                    status, result = initial.result(timeout=5)
                    self.wait_job()
        self.assertEqual(status, 202, result)

    def test_running_local_index_rejects_rescans_without_queueing(self):
        entered, release = threading.Event(), threading.Event()
        discover = self.service.discover

        def blocked_discover(**kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("fixture local job was not released")
            return discover(**kwargs)

        self.service.discover = blocked_discover
        try:
            self.assertEqual(self.call("/api/search/index", {})[0], 202)
            self.assertTrue(entered.wait(3))
            before = self.atlas_path.read_bytes()
            for endpoint in ("/api/rescan", "/api/assets/rescan"):
                status, result = self.call(endpoint, {})
                self.assertEqual(status, 409, result)
            self.scanner.assert_not_called()
            self.assertFalse(self.service.refresh_requested)
            self.assertEqual(self.atlas_path.read_bytes(), before)
        finally:
            release.set()
            self.wait_job()
        # A rejected busy attempt must not leave the admission lock held.
        status, result = self.call("/api/assets/rescan", {})
        self.assertEqual(status, 202, result)
        self.wait_job()
        self.embedding.assert_not_called()

    def test_assets_rescan_updates_instruction_map_catalog_and_local_index(self):
        self.assert_rescan_updates_snapshots("/api/assets/rescan", 202)

    def test_legacy_rescan_keeps_stdout_and_updates_both_snapshots(self):
        self.assert_rescan_updates_snapshots("/api/rescan", 200)


if __name__ == "__main__":
    unittest.main()
