"""Search engine tests; all later embedding vectors are synthetic fixtures."""
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import tempfile
import threading
import tracemalloc
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from atlas.search_index import SearchIndex, EmbeddingError


class SyntheticEmbeddingServer:
    """Real loopback HTTP, deliberately synthetic vectors, never a cloud provider."""
    def __init__(self):
        self.requests = []
        self.responder = self.normal_response
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fixture.requests.append({"path": self.path, "body": body,
                                         "authorization": self.headers.get("Authorization"),
                                         "userAgent": self.headers.get("User-Agent")})
                status, headers, payload = fixture.responder(body)
                raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
                if status is None:
                    self.wfile.write(raw)
                    return
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.settings = dict(baseUrl="http://127.0.0.1:%s/v1" % self.httpd.server_port,
                             model="synthetic-fixture-model", apiKey="fixture-secret-key",
                             dimensions=2, categories=["instruction", "skill"], batchSize=16)

    @staticmethod
    def normal_response(body):
        data = []
        for i, text in enumerate(body["input"]):
            vector = [0.0, 1.0] if "vehicle" in text else [1.0, 0.0]
            data.append(dict(index=i, embedding=vector))
        # Deliberately reverse indices to test provider response ordering.
        return 200, {}, dict(data=list(reversed(data)))

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


class SearchIndexTests(unittest.TestCase):
    def setUp(self):
        scratch = os.path.expanduser("~/.hermes/cache/scratch")
        Path(scratch).mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas-search-test-", dir=scratch)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "index.sqlite"
        self.index = SearchIndex(self.db_path)
        self.reads = []

    def file(self, name, text, **metadata):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        item = dict(path=str(path), name=path.name, platform="Claude",
                    category="instruction", project="atlas")
        item.update(metadata)
        return item

    def reader(self, entry):
        self.reads.append(entry["path"])
        path = Path(entry["path"])
        raw = path.read_bytes()
        stat = path.stat()
        return dict(content=raw.decode("utf-8"), truncated=False,
                    sha256=hashlib.sha256(raw).hexdigest(), mtime=stat.st_mtime,
                    bytes=stat.st_size)

    def provider(self):
        server = SyntheticEmbeddingServer()
        self.addCleanup(server.close)
        return server

    def test_sync_search_persists_local_keyword_index(self):
        item = self.file("AGENTS.md", "# Rules\n\nUse deterministic testing.\n")
        stats = self.index.sync([item], self.reader)
        self.assertEqual(stats["files"], 1)
        self.assertEqual(stats["added"], 1)
        result = SearchIndex(self.db_path).search("deterministic")
        self.assertEqual(result["mode"], "keyword")
        self.assertEqual(result["total"], 1)
        hit = result["results"][0]
        self.assertEqual(hit["path"], item["path"])
        self.assertEqual(hit["name"], "AGENTS.md")
        self.assertEqual(hit["platform"], "Claude")
        self.assertEqual(hit["category"], "instruction")
        self.assertEqual(hit["project"], "atlas")
        self.assertEqual(hit["matchType"], "keyword")
        self.assertIn("deterministic", hit["snippet"])
        self.assertLessEqual(hit["startLine"], 3)
        self.assertGreaterEqual(hit["endLine"], 3)
        self.assertGreater(hit["score"], 0)
        self.assertEqual(self.index.status()["vectors"], 0)

    def test_empty_vector_status_does_not_probe_every_chunk(self):
        item = self.file('AGENTS.md', '# Synthetic status fixture\n')
        self.index.sync([item], self.reader)
        with self.index._connect() as db:
            db.executemany('INSERT INTO chunks(path,start_line,end_line,text,text_sha256) VALUES (?,?,?,?,?)',
                           [(item['path'], 1, 1, 'fixture', 'fixture-%s' % i) for i in range(4096)])
        connect = self.index._connect
        steps = []
        @contextmanager
        def measured_connection():
            with connect() as db:
                db.set_progress_handler(lambda: steps.append(1) or 0, 100)
                yield db
        with patch.object(self.index, '_connect', measured_connection):
            status = self.index.status()
        self.assertEqual(status['chunks'], 4097)
        self.assertEqual(status['embeddedChunks'], 0)
        self.assertLess(len(steps), 50, 'Empty vector status must not run one lookup per chunk')

    def test_status_counts_chunks_once_across_embedding_profiles(self):
        items = [self.file('a.md', 'same synthetic text'), self.file('b.md', 'same synthetic text'),
                 self.file('c.md', 'not embedded')]
        self.index.sync(items, self.reader)
        with self.index._connect() as db:
            digest = db.execute('SELECT text_sha256 FROM chunks WHERE path=?', (items[0]['path'],)).fetchone()[0]
            for profile in ('fixture-model-a', 'fixture-model-b'):
                db.execute('INSERT INTO profiles VALUES (?,?)', (profile, 1))
                db.execute('INSERT INTO vectors VALUES (?,?,?)', (profile, digest, b'fixture-vector'))
            db.execute('INSERT INTO vectors VALUES (?,?,?)', ('fixture-model-a', 'orphan-fixture-hash', b'fixture-vector'))
        status = self.index.status()
        self.assertEqual(status['chunks'], 3)
        self.assertEqual(status['vectors'], 3)
        self.assertEqual(status['profiles'], 2)
        self.assertEqual(status['embeddedChunks'], 2, 'Shared content and duplicate profiles must not inflate chunk counts')

    def test_limits_bound_materialized_text_not_only_response_length(self):
        files = [self.file("large-%s.md" % i, ("needle document-%s " % i) * 700) for i in range(100)]
        total = self.index.sync(files, self.reader)["chunks"]
        tracemalloc.start()
        result = self.index.search("needle", limit=1)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertEqual(result["total"], total)
        self.assertEqual(len(result["results"]), 1)
        self.assertLess(peak, 600000, "Keyword limit must bound materialized matching text")
        provider = self.provider()
        tracemalloc.start()
        # Zero uploads isolates pending-selection memory from urllib's bounded
        # 16 MiB HTTP response buffer (HTTP batching has separate tests).
        progress = self.index.embed_pending(provider.settings, max_chunks=0)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertEqual(progress["embedded"], 0)
        self.assertEqual(provider.requests, [])
        self.assertGreater(progress["pending"], 1)
        self.assertLess(peak, 600000, "Embedding cap must bound materialized pending text")

    def test_restricted_unchanged_files_are_purged_before_fast_path(self):
        entry = self.file("notes.md", "permission-change-marker", searchable=True)
        self.index.sync([entry], self.reader)
        provider = self.provider()
        self.index.embed_pending(provider.settings)
        self.assertEqual(self.index.status()["vectors"], 1)
        self.reads.clear()
        self.index.sync([dict(entry, searchable=False)], self.reader)
        self.assertEqual(self.reads, [])
        self.assertEqual(self.index.search("permission-change-marker")["total"], 0)
        self.assertEqual(self.index.status()["vectors"], 0)

    def test_authorization_is_checked_before_each_actual_http_batch(self):
        entries = [self.file("a.md", "first document"), self.file("b.md", "second document")]
        self.index.sync(entries, self.reader)
        provider = self.provider()
        def authorize():
            if provider.requests:
                raise EmbeddingError("Cloud authorization revoked")
        settings = dict(provider.settings, batchSize=1, _authorize=authorize)
        with self.assertRaises(EmbeddingError):
            self.index.embed_pending(settings)
        self.assertEqual(len(provider.requests), 1)

    def test_real_settings_revocation_preserves_committed_batch_and_stops_http(self):
        from atlas.asset_settings import SettingsStore
        entries = [self.file("a.md", "first document"), self.file("b.md", "second document")]
        self.index.sync(entries, self.reader)
        provider = self.provider()
        store = SettingsStore(self.root / "preferences")
        store.save(dict(provider.settings, batchSize=1))
        def revoke(_stats):
            store.save({"clearApiKey": True, "categories": []})
        with self.assertRaisesRegex(EmbeddingError, "授权"):
            self.index.embed_pending(store.authorized(), progress=revoke)
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(self.index.status()["vectors"], 1)
        self.assertFalse(store.public()["apiKeyConfigured"])

    def test_restricted_flag_purges_without_reading_unchanged_content(self):
        entry = self.file("notes.md", "restricted-note-marker")
        self.index.sync([entry], self.reader)
        self.reads.clear()
        result = self.index.sync([dict(entry, restricted=True)], self.reader)
        self.assertEqual(self.reads, [])
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(result["chunks"], 0)
        self.assertEqual(self.index.search("restricted-note-marker")["total"], 0)

    def test_two_character_cjk_uses_index_candidates_including_column_tails(self):
        from contextlib import contextmanager
        from unittest.mock import patch
        paragraph = "irrelevant English context only.\n" * 32 + "\n"
        entries = [self.file("large.md", paragraph * 2400)]
        matches = [self.file("exact.md", "记忆"), self.file("tail.md", "内容记忆"),
                   self.file("middle.md", '有关记忆"的信息'), self.file("记忆", "English body")]
        self.index.sync(entries + matches, self.reader)
        original = self.index._connect
        steps = [0]
        @contextmanager
        def bounded_connection():
            with original() as db:
                def progress():
                    steps[0] += 100
                    return int(steps[0] > 60000)
                db.set_progress_handler(progress, 100)
                yield db
        with patch.object(self.index, "_connect", bounded_connection):
            answer = self.index.search("记忆")
        self.assertEqual(answer["total"], 4)
        self.assertEqual({r["path"] for r in answer["results"]}, {entry["path"] for entry in matches})
        filtered = self.index.search("记忆", filters={"path": matches[0]["path"]}, limit=1)
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(len(filtered["results"]), 1)

    def test_incremental_sync_skips_reads_and_removes_stale_content(self):
        first = self.file("first.md", "obsolete instruction")
        second = self.file("second.md", "persistent instruction")
        self.index.sync([first, second], self.reader)
        self.reads.clear()
        stats = self.index.sync([first, second, first], self.reader)
        self.assertEqual(self.reads, [])
        self.assertEqual(stats["unchanged"], 2)
        original = Path(first["path"]).stat()
        Path(first["path"]).write_text("replaced instruction", encoding="utf-8")
        os.utime(first["path"], ns=(original.st_atime_ns, original.st_mtime_ns))
        first = dict(first, platform="Codex", project="renamed")
        stats = self.index.sync([first], self.reader)
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(stats["deleted"], 1)
        self.assertEqual(self.index.search("obsolete")["total"], 0)
        self.assertEqual(self.index.search("persistent")["total"], 0)
        hit = self.index.search("replaced")["results"][0]
        self.assertEqual(hit["platform"], "Codex")
        self.assertEqual(hit["project"], "renamed")
        self.assertEqual(self.index.sync([], self.reader)["files"], 0)

    def test_cjk_short_queries_identifiers_and_paths_are_literal(self):
        item = self.file("deep/agent_rules.md", "中文向量检索配置\nuse snake_case.identifier\n")
        other = self.file("other.md", "irrelevant OR unrelated")
        self.index.sync([item, other], self.reader)
        for query in ("向量检索", "向量", "量", "snake_case.identifier", "agent_rules.md",
                      "deep/agent_rules.md"):
            with self.subTest(query=query):
                result = self.index.search(query)
                self.assertEqual(result["total"], 1)
                self.assertEqual(result["results"][0]["path"], item["path"])
        for query in ('" OR *', "text:irrelevant", "irrelevant NOT unrelated", "' OR 1=1 --"):
            with self.subTest(query=query):
                self.assertEqual(self.index.search(query)["total"], 0)

    def test_chunked_snippets_keep_source_lines_without_silent_file_cap(self):
        text = "前" * 3600 + "long_line_marker" + "后" * 1000 + "\n"
        text += "second_line_marker\n\n" + ("paragraph " * 150 + "\n\n") * 270
        text += "last_file_marker\n"
        item = self.file("long.md", text)
        stats = self.index.sync([item], self.reader)
        self.assertGreater(stats["chunks"], 256)
        for query, expected_line in (("long_line_marker", 1), ("second_line_marker", 2),
                                     ("last_file_marker", 544)):
            with self.subTest(query=query):
                hit = self.index.search(query)["results"][0]
                self.assertIn(query, hit["snippet"])
                self.assertLessEqual(len(hit["snippet"]), 500)
                self.assertLessEqual(hit["startLine"], expected_line)
                self.assertGreaterEqual(hit["endLine"], expected_line)
        self.assertEqual(stats["truncatedFiles"], 0)

    def test_filters_and_total_apply_before_result_limit(self):
        files = [self.file("first.md", "needle", platform="Codex", project="one"),
                 self.file("second.md", "needle", category="skill", project="two"),
                 self.file("third.md", "needle", category="config")]
        self.index.sync(files, self.reader)
        self.assertEqual(self.index.search("needle", limit=1)["total"], 3)
        checks = [({"platform": "Codex"}, [0]), ({"project": "two"}, [1]),
                  ({"category": "config"}, [2]),
                  ({"categories": ["skill", "config"]}, [1, 2]),
                  ({"category": ["instruction", "skill"]}, [0, 1]),
                  ({"path": files[1]["path"]}, [1]),
                  ({"platform": "Claude", "project": "two", "category": "skill"}, [1]),
                  ({"project": "' OR 1=1 --"}, []), ({"categories": []}, [])]
        for filters, expected in checks:
            with self.subTest(filters=filters):
                result = self.index.search("needle", filters=filters)
                self.assertEqual({r["path"] for r in result["results"]},
                                 {files[i]["path"] for i in expected})
                self.assertEqual(result["total"], len(expected))

    def test_explicit_embeddings_persist_real_http_protocol_and_cosine_search(self):
        provider = self.provider()
        files = [self.file("cat.md", "feline animal"),
                 self.file("car.md", "vehicle wheel")]
        self.index.sync(files, self.reader)
        self.assertEqual(provider.requests, [])
        stats = self.index.embed_pending(provider.settings)
        self.assertEqual(stats["embedded"], 2)
        self.assertEqual(stats["pending"], 0)
        self.assertEqual(self.index.status()["vectors"], 2)
        request = provider.requests[0]
        self.assertEqual(request["path"], "/v1/embeddings")
        self.assertEqual(request["authorization"], "Bearer fixture-secret-key")
        self.assertTrue(request["userAgent"].startswith("curl/"))
        self.assertEqual(request["body"]["model"], "synthetic-fixture-model")
        self.assertEqual(request["body"]["dimensions"], 2)
        self.assertEqual(request["body"]["encoding_format"], "float")
        self.assertEqual(set(request["body"]["input"]), {"feline animal", "vehicle wheel"})
        result = SearchIndex(self.db_path).search("kitten", mode="vector", settings=provider.settings)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["results"][0]["path"], files[0]["path"])
        self.assertAlmostEqual(result["results"][0]["score"], 1.0)
        self.assertAlmostEqual(result["results"][1]["score"], 0.0)
        self.assertEqual(result["results"][0]["matchType"], "vector")
        self.assertEqual(provider.requests[-1]["body"]["input"], ["kitten"])
        self.index.search("feline", settings=provider.settings)
        self.assertEqual(len(provider.requests), 2)

    def test_hybrid_uses_reciprocal_rank_fusion_and_explains_offline_fallback(self):
        provider = self.provider()
        files = [self.file("a.md", "kitten vehicle wheel"),
                 self.file("b.md", "feline animal"), self.file("c.md", "unrelated vehicle")]
        self.index.sync(files, self.reader)
        fallback = self.index.search("kitten", mode="hybrid", settings=provider.settings)
        self.assertEqual(fallback["mode"], "hybrid")
        self.assertIn("keyword", fallback["warning"].lower())
        self.assertEqual(fallback["results"][0]["matchType"], "keyword")
        with self.assertRaises(EmbeddingError):
            self.index.search("kitten", mode="vector", settings=provider.settings)
        self.assertEqual(provider.requests, [])
        self.index.embed_pending(provider.settings)
        result = self.index.search("kitten", mode="hybrid", settings=provider.settings)
        self.assertNotIn("warning", result)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["results"][0]["path"], files[0]["path"])
        self.assertEqual(result["results"][0]["matchType"], "hybrid")
        self.assertAlmostEqual(result["results"][0]["score"], 1 / 61 + 1 / 62)
        provider.responder = lambda body: (503, {}, {"error": "fixture-secret-key"})
        fallback = self.index.search("kitten", mode="hybrid", settings=provider.settings)
        self.assertEqual(fallback["results"][0]["path"], files[0]["path"])
        self.assertIn("keyword", fallback["warning"].lower())
        self.assertNotIn("fixture-secret-key", json.dumps(fallback))

    def test_duplicate_text_cache_and_stale_vector_invalidation(self):
        provider = self.provider()
        first = self.file("first.md", "feline animal")
        duplicate = self.file("duplicate.md", "feline animal")
        self.index.sync([first, duplicate], self.reader)
        self.assertEqual(self.index.embed_pending(provider.settings)["embedded"], 1)
        self.assertEqual(self.index.status()["embeddedChunks"], 2)
        self.assertEqual(self.index.embed_pending(provider.settings)["embedded"], 0)
        self.assertEqual(len(provider.requests), 1)
        # Updating one duplicate must retain the other file's reusable vector.
        Path(first["path"]).write_text("vehicle replacement", encoding="utf-8")
        self.index.sync([first, duplicate], self.reader)
        result = self.index.search("animal", mode="vector", settings=provider.settings)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["path"], duplicate["path"])
        # Removing the last reference must purge the outdated content vector.
        self.index.sync([first], self.reader)
        self.assertEqual(self.index.status()["vectors"], 0)
        self.assertEqual(self.index.embed_pending(provider.settings)["embedded"], 1)
        self.index.sync([], self.reader)
        self.assertEqual(self.index.status()["vectors"], 0)

    def test_truncated_preview_is_reported_and_unreadable_file_cannot_stay_stale(self):
        first = self.file("preview.md", "visible preview\nunread tail")
        second = self.file("second.md", "stale phrase")

        def bounded(entry):
            data = self.reader(entry)
            if entry["path"] == first["path"]:
                data.update(content="visible preview", truncated=True)
            return data

        stats = self.index.sync([first, second], bounded)
        self.assertEqual(stats["truncatedFiles"], 1)
        self.assertEqual(stats["truncatedPaths"], [first["path"]])
        self.assertEqual(self.index.status()["truncatedPaths"], [first["path"]])
        self.assertEqual(self.index.search("unread tail")["total"], 0)
        Path(second["path"]).unlink()
        stats = self.index.sync([first, second], bounded)
        self.assertEqual(stats["errors"], 1)
        self.assertEqual(stats["files"], 1)
        self.assertEqual(self.index.search("stale phrase")["total"], 0)

    def test_network_has_no_write_lock_and_cannot_resurrect_deleted_vectors(self):
        provider = self.provider()
        item = self.file("remove.md", "feline animal")
        self.index.sync([item], self.reader)
        entered, release = threading.Event(), threading.Event()
        errors = []

        def delayed(body):
            entered.set()
            release.wait(timeout=5)
            return provider.normal_response(body)

        def embed():
            try:
                self.index.embed_pending(provider.settings)
            except Exception as exc:
                errors.append(exc)

        provider.responder = delayed
        worker = threading.Thread(target=embed)
        worker.start()
        self.assertTrue(entered.wait(timeout=5))
        cleared = threading.Event()

        def clear():
            SearchIndex(self.db_path).sync([], self.reader)
            cleared.set()

        cleaner = threading.Thread(target=clear)
        cleaner.start()
        try:
            self.assertTrue(cleared.wait(timeout=2), "SQLite/write lock held across HTTP")
        finally:
            release.set()
            cleaner.join(timeout=5)
            worker.join(timeout=5)
        self.assertEqual(errors, [])
        self.assertEqual(self.index.status()["files"], 0)
        self.assertEqual(self.index.status()["vectors"], 0)

    def test_embedding_categories_batch_limit_and_progress_are_explicit(self):
        provider = self.provider()
        files = [self.file("a.md", "feline"), self.file("b.md", "vehicle", category="skill"),
                 self.file("c.md", "sensitive config", category="config"),
                 self.file("d.md", "sensitive session", category="session"),
                 self.file("e.md", "sensitive log", category="log"),
                 self.file("f.md", "\n\t   ")]
        self.index.sync(files, self.reader)
        settings = dict(provider.settings, batchSize=1)
        del settings["categories"]  # Engine defaults must also be privacy-safe.
        progress = []
        result = self.index.embed_pending(settings, max_chunks=1, progress=progress.append)
        self.assertEqual(result["embedded"], 1)
        self.assertEqual(result["pending"], 1)
        self.assertEqual(len(progress), 1)
        self.assertEqual(progress[0]["embedded"], 1)
        result = self.index.embed_pending(settings)
        self.assertEqual(result["embedded"], 1)
        self.assertEqual(result["pending"], 0)
        self.assertEqual([r["body"]["input"] for r in provider.requests], [["feline"], ["vehicle"]])
        opted_in = dict(settings, categories=["config"])
        self.assertEqual(self.index.embed_pending(opted_in)["embedded"], 1)
        self.assertEqual(provider.requests[-1]["body"]["input"], ["sensitive config"])
        result = self.index.search("kitten", mode="vector", settings=settings)
        self.assertEqual({r["category"] for r in result["results"]}, {"instruction", "skill"})

    def test_search_rejects_invalid_modes_limits_and_filters(self):
        self.index.sync([self.file("test.md", "needle")], self.reader)
        for kwargs in ({"mode": "pretend-vector"}, {"limit": -1}, {"limit": True},
                       {"limit": "1"}, {"filters": "category"}, {"filters": {"unknown": "x"}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.index.search("needle", **kwargs)
        for query in (None, 123, "\x00needle", "x" * 10001):
            with self.subTest(query=repr(query)[:30]), self.assertRaises(ValueError):
                self.index.search(query)
        self.assertEqual(self.index.search(" ")["results"], [])
        result = self.index.search("needle", limit=0)
        self.assertEqual(result["results"], [])
        self.assertEqual(result["total"], 1)


    def test_provider_signature_isolation_and_no_credentials_in_sqlite(self):
        provider = self.provider()
        self.index.sync([self.file("a.md", "feline")], self.reader)
        self.index.embed_pending(provider.settings)
        for changes in ({"model": "different-model"}, {"dimensions": 3},
                        {"baseUrl": provider.settings["baseUrl"] + "/other"}):
            with self.subTest(changes=changes), self.assertRaises(EmbeddingError):
                self.index.search("cat", mode="vector", settings=dict(provider.settings, **changes))
        self.assertEqual(len(provider.requests), 1)
        # Rotating a key must not invalidate the same embedding space.
        rotated = dict(provider.settings, apiKey="different-fixture-key")
        self.assertEqual(self.index.embed_pending(rotated)["embedded"], 0)
        self.assertEqual(self.index.search("cat", mode="vector", settings=rotated)["total"], 1)
        self.assertEqual(provider.requests[-1]["authorization"], "Bearer different-fixture-key")
        db = sqlite3.connect(self.db_path)
        try:
            dump = "\n".join(db.iterdump())
            schema = db.execute("SELECT sql FROM sqlite_master WHERE name='chunks_fts'").fetchone()[0]
        finally:
            db.close()
        self.assertIn("fts5", schema.lower())
        for secret in ("fixture-secret-key", "different-fixture-key", "apiKey", "synthetic-fixture-model",
                       provider.settings["baseUrl"]):
            self.assertNotIn(secret, dump)
            for artifact in self.root.glob("index.sqlite*"):
                self.assertNotIn(secret.encode(), artifact.read_bytes())

    def test_complete_embedding_batches_are_validated_before_any_write(self):
        provider = self.provider()
        old = self.file("old.md", "retained animal")
        self.index.sync([old], self.reader)
        self.index.embed_pending(provider.settings)
        files = [old, self.file("a.md", "new animal"), self.file("b.md", "new vehicle")]
        self.index.sync(files, self.reader)
        valid = {"index": 0, "embedding": [1.0, 0.0]}
        invalid_payloads = [
            {"data": [valid]},
            {"data": [valid, valid]},
            {"data": [valid, {"index": 2, "embedding": [0.0, 1.0]}]},
            {"data": [valid, {"index": True, "embedding": [0.0, 1.0]}]},
            {"data": [valid, {"index": 1, "embedding": [0.0, 1.0, 0.0]}]},
            {"data": [valid, {"index": 1, "embedding": [0.0, 0.0]}]},
            {"data": [valid, {"index": 1, "embedding": [float("nan"), 1.0]}]},
            {"data": [valid, {"index": 1, "embedding": [float("inf"), 1.0]}]},
            {"data": [valid, {"index": 1, "embedding": [True, 1.0]}]},
            {"data": [valid, {"index": 1, "embedding": "fixture-secret-key"}]},
            {"data": [valid, {"index": 1}]}, {"data": None}, {"data": [None, None]},
            b"not JSON fixture-secret-key", b'{"data":',
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=str(payload)[:100]):
                provider.responder = lambda body, value=payload: (200, {}, value)
                with self.assertRaises(EmbeddingError) as caught:
                    self.index.embed_pending(provider.settings)
                self.assertNotIn("fixture-secret-key", str(caught.exception))
                self.assertEqual(self.index.status()["vectors"], 1)
                self.assertEqual(self.index.status()["embeddedChunks"], 1)
        provider.responder = provider.normal_response
        self.assertEqual(self.index.embed_pending(provider.settings)["embedded"], 2)

    def test_provider_redirects_and_http_errors_never_expose_auth_or_body(self):
        source, destination = self.provider(), self.provider()
        self.index.sync([self.file("a.md", "feline")], self.reader)
        for status in (301, 302, 303, 307, 308, 401, 429, 500):
            with self.subTest(status=status):
                source.responder = lambda body, status=status: (
                    status, {"Location": destination.settings["baseUrl"] + "/embeddings"},
                    {"error": "fixture-secret-key provider-private-error"})
                with self.assertRaises(EmbeddingError) as caught:
                    self.index.embed_pending(source.settings)
                self.assertNotIn("fixture-secret-key", str(caught.exception))
                self.assertNotIn("provider-private-error", str(caught.exception))
                self.assertIn(str(status), str(caught.exception))
                self.assertEqual(self.index.status()["vectors"], 0)
        self.assertEqual(destination.requests, [])

    def test_settings_reject_unsafe_endpoints_and_invalid_values_before_network(self):
        provider = self.provider()
        self.index.sync([self.file("a.md", "feline")], self.reader)
        bad_urls = ["http://example.com/v1", "http://localhost.evil/v1", "http://0.0.0.0/v1",
                    "file:///private/file", "ftp://example.com/v1", "https://user:secret@example.com/v1",
                    "https://example.com/v1?key=fixture-secret-key", "https://example.com/v1#secret",
                    "https://example.com:invalid/v1", "https://example.com\n/v1", "https://"]
        bad_settings = [dict(provider.settings, baseUrl=url) for url in bad_urls]
        bad_settings += [None, {}, dict(provider.settings, apiKey=""),
                         dict(provider.settings, dimensions=0), dict(provider.settings, dimensions=True),
                         dict(provider.settings, dimensions=2.5), dict(provider.settings, categories="config"),
                         dict(provider.settings, batchSize=0), dict(provider.settings, batchSize=257),
                         dict(provider.settings, apiKey="fixture-secret-key\nAuthorization: injected")]
        for settings in bad_settings:
            with self.subTest(settings=str(settings)[:60]), self.assertRaises(EmbeddingError) as caught:
                self.index.embed_pending(settings)
            self.assertNotIn("fixture-secret-key", str(caught.exception))
        self.assertEqual(provider.requests, [])

    def test_omitted_dimensions_are_learned_and_explicit_endpoint_is_not_doubled(self):
        provider = self.provider()
        settings = dict(provider.settings, baseUrl=provider.settings["baseUrl"] + "/embeddings")
        del settings["dimensions"]
        self.index.sync([self.file("a.md", "feline")], self.reader)
        self.index.embed_pending(settings)
        self.assertNotIn("dimensions", provider.requests[0]["body"])
        self.assertEqual(provider.requests[0]["path"], "/v1/embeddings")
        provider.responder = lambda body: (200, {}, {"data": [{"index": 0, "embedding": [1, 0, 0]}]})
        with self.assertRaises(EmbeddingError):
            self.index.search("cat", mode="vector", settings=settings)
        self.assertEqual(self.index.status()["vectors"], 1)


    def test_vector_filters_return_empty_without_uploading_a_query_when_no_match(self):
        provider = self.provider()
        files = [self.file("a.md", "feline", platform="Claude", project="one"),
                 self.file("b.md", "vehicle", platform="Codex", category="skill", project="two")]
        self.index.sync(files, self.reader)
        self.index.embed_pending(provider.settings)
        for filters in ({"platform": "Codex"}, {"project": "two"}, {"category": "skill"},
                        {"categories": ["skill"]}, {"path": files[1]["path"]}):
            with self.subTest(filters=filters):
                result = self.index.search("cat", mode="vector", filters=filters, settings=provider.settings)
                self.assertEqual([r["path"] for r in result["results"]], [files[1]["path"]])
        requests = len(provider.requests)
        for mode in ("vector", "hybrid"):
            result = self.index.search("cat", mode=mode, filters={"project": "missing"}, settings=provider.settings)
            self.assertEqual(result["total"], 0)
            self.assertEqual(result["results"], [])
            self.assertNotIn("warning", result)
        self.assertEqual(len(provider.requests), requests)


    def test_malformed_http_status_is_sanitized_instead_of_leaking_server_text(self):
        provider = self.provider()
        self.index.sync([self.file("a.md", "feline")], self.reader)
        provider.responder = lambda body: (None, {}, b"fixture-secret-key BAD STATUS\r\n\r\n")
        with self.assertRaises(EmbeddingError) as caught:
            self.index.embed_pending(provider.settings)
        self.assertNotIn("fixture-secret-key", str(caught.exception))
        self.assertEqual(self.index.status()["vectors"], 0)


    def test_later_upload_batches_recheck_category_changes(self):
        provider = self.provider()
        first = self.file("a.md", "feline")
        second = self.file("b.md", "newly private vehicle")
        self.index.sync([first, second], self.reader)

        def reclassify(progress):
            self.index.sync([first, dict(second, category="config")], self.reader)

        stats = self.index.embed_pending(dict(provider.settings, batchSize=1), progress=reclassify)
        self.assertEqual(stats["embedded"], 1)
        self.assertEqual(stats["pending"], 0)
        self.assertEqual([r["body"]["input"] for r in provider.requests], [["feline"]])


    def test_concurrent_status_observes_one_complete_snapshot(self):
        files = [self.file("%s.md" % n, "one chunk") for n in range(8)]

        def truncated(entry):
            return dict(self.reader(entry), truncated=True)

        barrier = threading.Barrier(2)
        errors = []

        def write_snapshots():
            try:
                barrier.wait()
                writer = SearchIndex(self.db_path)
                for _ in range(60):
                    writer.sync(files, truncated)
                    writer.sync([], truncated)
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=write_snapshots)
        worker.start()
        barrier.wait()
        try:
            for _ in range(200):
                stats = self.index.status()
                self.assertEqual(stats["files"], stats["chunks"])
                self.assertEqual(stats["files"], stats["truncatedFiles"])
        finally:
            worker.join(timeout=10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])


    def test_short_query_name_and_path_matches_are_boosted_above_content(self):
        content = self.file("a.md", "云计算 instruction")
        path = self.file("云/b.md", "other instruction")
        name = self.file("云.md", "other instruction")
        self.index.sync([content, path, name], self.reader)
        results = self.index.search("云")["results"]
        self.assertEqual([r["path"] for r in results], [name["path"], path["path"], content["path"]])
        self.assertGreater(results[0]["score"], results[1]["score"])
        self.assertGreater(results[1]["score"], results[2]["score"])

    def test_exact_cosine_normalizes_provider_vectors_instead_of_using_raw_dot_products(self):
        provider = self.provider()
        files = [self.file("a.md", "feline"), self.file("b.md", "vehicle")]
        fixture_vectors = {"feline": [3.0, 4.0], "vehicle": [0.0, -5.0], "direction": [0.0, 2.0]}
        provider.responder = lambda body: (200, {}, {"data": [
            {"index": i, "embedding": fixture_vectors[text]} for i, text in enumerate(body["input"])]})
        self.index.sync(files, self.reader)
        self.index.embed_pending(provider.settings)
        results = self.index.search("direction", mode="vector", settings=provider.settings)["results"]
        self.assertAlmostEqual(results[0]["score"], 0.8)
        self.assertAlmostEqual(results[1]["score"], -1.0)


if __name__ == "__main__":
    unittest.main()
