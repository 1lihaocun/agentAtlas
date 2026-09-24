"""Repeated scans are independent; overlap must not widen depth or read twice."""
from pathlib import Path
from collections import Counter
import os
import tempfile
import unittest
from unittest.mock import patch

from atlas import scan


class ScanRefreshTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path.home() / '.hermes/cache/scratch')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.root = self.home / 'projects'
        self.root.mkdir()
        for name, value in [('HOME', str(self.home)), ('ROOTS', [str(self.root)])]:
            handle = patch.object(scan, name, value)
            handle.start()
            self.addCleanup(handle.stop)

    def put(self, relative, text='# Synthetic instruction\n'):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def test_overlapping_roots_do_not_enumerate_covered_subtrees_again(self):
        child = self.put('repo/AGENTS.md')
        deeper = self.put('repo/a/b/CLAUDE.md', '# Deep fixture\n')
        self.put('repo/a/b/outside/AGENTS.md')
        peer = self.put('peer/GEMINI.md', '# Peer fixture\n')
        visited = Counter()
        scandir = os.scandir
        def measured(path):
            visited[str(path)] += 1
            return scandir(path)
        with patch.object(scan, 'ROOTS', [str(child.parent), str(self.root), str(child.parent)]), \
                patch.object(scan, 'DEFAULT_DEPTH', 2), patch('os.scandir', side_effect=measured):
            data = scan.scan()
        self.assertEqual({f['path'] for f in data['files']}, {str(child), str(deeper), str(peer)})
        self.assertTrue(visited)
        self.assertEqual(max(visited.values()), 1, 'Overlapping roots must reuse sufficient traversal coverage')

    def test_parent_then_child_retains_the_childs_full_depth_budget(self):
        shallow = self.put('repo/AGENTS.md')
        deep = self.put('repo/a/b/CLAUDE.md', '# Independent depth\n')
        self.put('repo/a/b/outside/AGENTS.md')
        roots = [str(self.root), str(shallow.parent)]
        with patch.object(scan, 'DEFAULT_DEPTH', 2):
            for ordering in (roots, list(reversed(roots))):
                with self.subTest(order=ordering), patch.object(scan, 'ROOTS', ordering):
                    data = scan.scan()
                self.assertEqual({f['path'] for f in data['files']}, {str(shallow), str(deep)})
        # Standalone calls have independent coverage too.
        self.assertEqual(scan.walk_root(str(shallow.parent), 2), scan.walk_root(str(shallow.parent), 2))

    def test_repeated_scan_does_not_reuse_old_duplicate_groups(self):
        first = self.put('a/AGENTS.md')
        second = self.put('b/CLAUDE.md')
        before = scan.scan()
        self.assertEqual(before['dupFiles'], 2)
        self.assertEqual(before['dupGroups'], [[str(first), str(second)]])
        second.unlink()
        after = scan.scan()
        self.assertEqual(after['totalFiles'], 1)
        self.assertEqual(after['dupGroups'], [], 'Deleted paths cannot survive in the next scan')
        self.assertEqual(after['dupFiles'], 0)
        self.assertEqual(after['uniqueFiles'], 1)
        self.assertFalse(after['files'][0]['dup'])
        again = scan.scan()
        for key in ('files', 'roots', 'dupGroups', 'projectCandidates', 'uniqueFiles'):
            self.assertEqual(after[key], again[key])


if __name__ == '__main__':
    unittest.main()
