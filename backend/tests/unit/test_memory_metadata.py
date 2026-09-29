
import importlib
from pathlib import Path
import tempfile
import unittest


class MemoryMetadataTests(unittest.TestCase):
    def parse(self, text, roots, **kwargs):
        module = importlib.import_module('atlas.memories.metadata')
        return module.parse_memory_metadata(text, '/memories/note.md', roots, **kwargs)

    def test_titles_and_project_mentions_have_source_lines_not_scope(self):
        text = ('---\ntitle: "项目回顾"\ndescription: 只作展示的说明\n---\n'
                '# Summary\n讨论 `/code/app/src/api.py`，不涉及 /code/apple。\n')
        result = self.parse(text, ['/code/app', '/code/apple-other'])
        self.assertEqual(result['title'], '项目回顾')
        self.assertEqual(result['description'], '只作展示的说明')
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['relatedProjects'], [{
            'path': '/code/app', 'name': 'app', 'basis': 'path_mention',
            'evidence': {'path': '/memories/note.md', 'line': 6}}])
        self.assertNotIn('appliesTo', result)
        self.assertNotIn('observation', result)

    def test_source_cwd_uses_deepest_known_root_and_never_parent_traversal(self):
        text = ('thread_id: fixture\ncwd: /code/app/subproject/src\n\n# Topic\n'
                'Bad reference `/code/app/../unrelated` and `/code/apple`\n')
        result = self.parse(text, ['/code/app', '/code/app/subproject'])
        self.assertEqual(result['relatedProjects'], [{
            'path': '/code/app/subproject', 'name': 'subproject', 'basis': 'source_cwd',
            'evidence': {'path': '/memories/note.md', 'line': 2}}])

    def test_collector_bounds_reads_caches_metadata_and_rechecks_live_changes(self):
        module = importlib.import_module('atlas.memories.metadata')
        from atlas.assets.catalog import read_text
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[3] / '.agentatlas/work')
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name).resolve() / 'note.md'
        path.write_text('# First\n' + 'x' * 50000)
        entry = {'path': str(path), 'name': path.name, 'category': 'memory',
                 'platform': 'codex', 'profile': 'default', 'searchable': True}
        calls = []
        def reader(record, max_bytes):
            calls.append(max_bytes)
            return read_text(record, max_bytes=max_bytes)
        self.assertTrue(hasattr(module, 'MemoryMetadata'), 'bounded metadata collector is missing')
        collector = module.MemoryMetadata(reader=reader)
        first, evidence = collector.collect([entry], [])
        self.assertEqual(first[str(path)]['title'], 'First')
        self.assertEqual(first[str(path)]['status'], 'partial')
        self.assertLessEqual(calls[0], 16384)
        self.assertEqual(evidence, {})
        first[str(path)]['title'] = 'caller mutation'
        second, _ = collector.collect([entry], [])
        self.assertEqual(second[str(path)]['title'], 'First')
        self.assertEqual(len(calls), 1)
        path.write_text('# Second\n')
        fresh, _ = collector.collect([entry], [])
        self.assertEqual(fresh[str(path)]['title'], 'Second')
        self.assertEqual(len(calls), 2)
        path.unlink()
        missing, _ = collector.collect([entry], [])
        self.assertEqual(missing[str(path)]['status'], 'unavailable')
        self.assertEqual(missing[str(path)]['title'], 'note.md')
        self.assertEqual(len(calls), 2)

    def test_nonmapping_frontmatter_does_not_break_other_files(self):
        module = importlib.import_module('atlas.memories.metadata')
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[3] / '.agentatlas/work') as temp:
            root = Path(temp).resolve()
            good = root / 'good.md'
            good.write_text('# 正常标题\n', encoding='utf-8')
            bad = root / 'bad.md'
            entries = [{'path': str(path), 'category': 'memory', 'searchable': True} for path in (good, bad)]
            for yaml_text in ('- a\n- b', 'scalar', '42'):
                bad.write_text('---\n' + yaml_text + '\n---\n正文', encoding='utf-8')
                result, _ = module.MemoryMetadata().collect(entries, [])
                self.assertEqual(result[str(good)]['title'], '正常标题')
                self.assertEqual(result[str(bad)]['status'], 'ready')
                self.assertEqual(result[str(bad)]['title'], 'bad.md')

    def test_truncated_utf8_boundary_keeps_a_valid_title(self):
        module = importlib.import_module('atlas.memories.metadata')
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[3] / '.agentatlas/work')
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name).resolve() / 'long.md'
        path.write_text('# 中文标题\n' + '汉' * 10000, encoding='utf-8')
        entry = {'path': str(path), 'category': 'memory', 'searchable': True}
        metadata, _ = module.MemoryMetadata().collect([entry], [])
        self.assertEqual(metadata[str(path)]['title'], '中文标题')
        self.assertEqual(metadata[str(path)]['status'], 'partial')

    def test_restrictions_and_nonregular_changes_evict_cached_metadata(self):
        module = importlib.import_module('atlas.memories.metadata')
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[3] / '.agentatlas/work')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        path = root / 'note.md'
        path.write_text('# Existing title')
        entry = {'path': str(path), 'category': 'memory', 'searchable': True}
        collector = module.MemoryMetadata()
        metadata, _ = collector.collect([entry], [])
        self.assertEqual(metadata[str(path)]['title'], 'Existing title')
        path.unlink()
        path.mkdir()
        metadata, _ = collector.collect([entry], [])
        self.assertEqual(metadata[str(path)]['status'], 'unavailable')
        self.assertNotIn(str(path), collector._cache)
        metadata, _ = collector.collect([dict(entry, searchable=False)], [])
        self.assertEqual(metadata[str(path)]['status'], 'metadata_only')
        with self.subTest('nonmemory entries are never read'):
            def forbidden(*args, **kwargs):
                raise AssertionError('must not read')
            collector = module.MemoryMetadata(reader=forbidden)
            self.assertEqual(collector.collect([dict(entry, category='config')], []), ({}, {}))

    def test_extension_declarations_are_bounded_local_evidence_not_public_bodies(self):
        module = importlib.import_module('atlas.memories.metadata')
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[3] / '.agentatlas/work')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        path = root / '.codex/memories/extensions/skysight/instructions.md'
        path.parent.mkdir(parents=True)
        path.write_text('# Skysight Memory Instructions\nUse resources during phase2.')
        entry = {'path': str(path), 'category': 'memory', 'platform': 'codex',
                 'profile': 'default', 'searchable': True}
        collector = module.MemoryMetadata()
        metadata, evidence = collector.collect([entry], [])
        self.assertIn(str(path), evidence)
        self.assertEqual(evidence[str(path)]['content'], path.read_text())
        self.assertEqual(len(evidence[str(path)]['sha256']), 64)
        self.assertFalse(evidence[str(path)]['truncated'])
        self.assertNotIn('content', metadata[str(path)])
        self.assertNotIn('Use resources', str(metadata))
        path.write_text('# Skysight\n' + 'x' * 50000)
        _, evidence = collector.collect([entry], [])
        self.assertTrue(evidence[str(path)]['truncated'])
        self.assertLessEqual(len(evidence[str(path)]['content'].encode()), 16384)
        metadata, evidence = collector.collect([dict(entry, searchable=False)], [])
        self.assertEqual(metadata[str(path)]['status'], 'metadata_only')
        self.assertEqual(evidence, {})


if __name__ == '__main__':
    unittest.main()
