from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from atlas.asset_service import AssetService
from atlas.asset_files import FileProblem


class RecordingIndex:
    def __init__(self):
        self.uploads = 0
        self.syncs = 0

    def sync(self, files, reader):
        self.syncs += 1
        return {"files": len(files), "chunks": 0, "vectors": 0}

    def status(self):
        return {"files": 0, "chunks": 0, "vectors": 0}

    def embed_pending(self, settings, max_chunks=256, progress=None):
        self.uploads += 1
        return {"embedded": 0}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.home() / ".hermes/cache/scratch")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.index = RecordingIndex()
        self.service = AssetService(self.root, lambda: {"files": []},
                                    discover_fn=lambda **kw: {"files": [], "totalFiles": 0},
                                    reader=lambda f: {}, index=self.index)

    def test_first_scan_does_not_grant_legacy_files_edit_permission(self):
        target = self.root / ".hermes/profiles/work/memories/MEMORY.md"
        target.parent.mkdir(parents=True)
        target.write_text("private profile")
        self.service.instruction_loader = lambda: {"files": [{"path": str(target)}]}
        with self.assertRaises(FileProblem):
            self.service.file_store.entry(str(target), write=True)

    def test_default_service_finds_asset_only_projects_in_bounded_scan_roots(self):
        scan_root = self.root / 'Documents/Code'
        expected = ['solo/.claude/skills/demo/SKILL.md',
                    'commands-only/.claude/commands/settings.md']
        excluded = ['cache/hidden/.claude/skills/demo/SKILL.md',
                    'node_modules/hidden/.claude/skills/demo/SKILL.md',
                    'credentials/hidden/.claude/skills/demo/SKILL.md',
                    'deep/a/b/c/d/e/.claude/skills/demo/SKILL.md']
        for name in expected + excluded:
            path = scan_root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture context')
        outside = self.root / 'not-authorized/.claude/skills/demo/SKILL.md'
        outside.parent.mkdir(parents=True)
        outside.write_text('not discovered')
        (scan_root / 'linked').symlink_to(outside.parents[3], target_is_directory=True)
        (scan_root / 'solo/.claude/skills/link').symlink_to(outside.parent, target_is_directory=True)
        from atlas import scan
        with patch.object(scan, 'HOME', str(self.root)), patch.object(scan, 'ROOTS', [str(scan_root)]), \
                patch.object(scan, 'DEFAULT_DEPTH', 4), patch('pathlib.Path.home', return_value=self.root):
            service = AssetService(self.root / 'service', lambda: {'files': []}, index=self.index)
            service._sync()
        self.assertEqual({row['path'] for row in service.files()},
                         {str(scan_root / path) for path in expected})
        candidates = service.snapshot().get('projectCandidates', [])
        self.assertEqual({row['path'] for row in candidates},
                         {str(scan_root / 'solo'), str(scan_root / 'commands-only')})
        self.assertTrue(all('asset_directory' in {source['kind'] for source in row['sources']}
                            for row in candidates))
        self.assertEqual(self.index.uploads, 0)

    def test_catalog_records_exact_instruction_snapshot_generation(self):
        snapshots = iter([{'generatedAt': 1234567890, 'files': []}, {'files': []}])
        self.service.instruction_loader = lambda: next(snapshots)
        self.service._sync()
        self.assertEqual(self.service.snapshot().get('instructionGeneratedAt'), 1234567890)
        self.service._sync()
        self.assertIn('instructionGeneratedAt', self.service.snapshot())
        self.assertIsNone(self.service.snapshot()['instructionGeneratedAt'])

    def test_cloud_revocation_stops_later_batches(self):
        self.service.settings.save({"baseUrl": "https://fixture.example/v1", "model": "fixture", "apiKey": "synthetic-key"})
        attempts = []
        def uploading(settings, max_chunks=256, progress=None):
            for batch in range(2):
                settings["_authorize"]()
                attempts.append(batch)
                self.service.settings.save({"categories": [], "clearApiKey": True})
            return {"embedded": 2}
        self.index.embed_pending = uploading
        self.service.start(embeddings=True, confirm_cloud=True)
        self.service.worker.join(5)
        self.assertEqual(attempts, [0])
        self.assertEqual(self.service.status()["job"]["stage"], "error")
        self.assertIn("授权", self.service.status()["job"]["error"])

    def test_local_rebuild_never_calls_embedding(self):
        self.service.start()
        self.service.worker.join(5)
        self.assertFalse(self.service.status()["job"]["running"])
        self.assertEqual(self.index.syncs, 1)
        self.assertEqual(self.index.uploads, 0)
        self.assertTrue((self.root / ".agentatlas" / "assets.json").is_file())

    def test_catalog_exclusions_cannot_be_reintroduced_by_legacy_fallback(self):
        self.service.instruction_loader = lambda: {"files": [{"path": str(self.root / "AGENTS.md")}]}
        self.service.start()
        self.service.worker.join(5)
        self.assertEqual(self.service.files(), [])

    def test_cloud_build_requires_explicit_consent_and_configuration(self):
        with self.assertRaises(FileProblem):
            self.service.start(embeddings=True)
        with self.assertRaises(FileProblem):
            self.service.start(embeddings=True, confirm_cloud=True)
        self.assertEqual(self.index.uploads, 0)

    def test_metadata_only_assets_are_not_index_errors(self):
        self.service.discover = lambda **kw: {"files": [{"path": "database.sqlite", "searchable": False}], "totalFiles": 1}
        self.service.start()
        self.service.worker.join(5)
        self.assertEqual(self.service.status()["job"]["localResult"]["files"], 0)
        self.assertEqual(self.service.status()["index"]["skippedFiles"], 1)

    def test_busy_rebuild_rejected_without_duplicate_cloud_jobs(self):
        release = threading.Event()
        self.addCleanup(release.set)
        def blocked_discovery(**kw):
            release.wait(3)
            return {"files": [], "totalFiles": 0}
        self.service.discover = blocked_discovery
        self.service.start()
        with self.assertRaises(FileProblem) as raised:
            self.service.start()
        self.assertEqual(raised.exception.status, 409)
        release.set()
        self.service.worker.join(5)
        self.assertIsNone(self.service.status()["job"]["error"])


if __name__ == "__main__":
    unittest.main()
