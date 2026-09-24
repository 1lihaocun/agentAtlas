"""Service integration for memory scope; resolver behavior has separate tests."""
import copy
import re
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from atlas.asset_service import AssetService


class MemoryServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path.home() / ".hermes/cache/scratch")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "code" / "local" / "my-project"
        self.instructions = [{"path": str(self.project / "src" / "deep" / "AGENTS.md"),
                              "dir": str(self.project / "src" / "deep"), "root": "local",
                              "proj": "my-project", "sub": "src/deep", "scope": "project"}]
        instruction = Path(self.instructions[0]['path'])
        instruction.parent.mkdir(parents=True)
        instruction.write_text('fixture instruction')
        (self.project / '.git').mkdir()
        home_patch = patch('pathlib.Path.home', return_value=self.root)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        self.service = AssetService(self.root, lambda: {"files": self.instructions},
                                    discover_fn=lambda **kw: {}, reader=lambda entry: {})
        self.service.catalog["files"] = [{"path": str(self.root / "MEMORY.md"),
                                          "category": "memory", "scope": "user"}]

    def test_memory_resolver_gets_catalog_known_roots_and_unchanged_filters(self):
        before = copy.deepcopy(self.service.catalog)
        calls = []
        def resolver(files, **kwargs):
            calls.append((files, kwargs))
            files[0]["category"] = "fixture-change"
            return {"files": [], "counts": {"matched": 0}}
        with patch.dict(sys.modules, {"atlas.memory_scope": types.SimpleNamespace(build_memory_view=resolver)}):
            result = self.service.memories(project=str(self.project), platform="hermes", profile="work")
        self.assertEqual(result["counts"]["matched"], 0)
        self.assertEqual(self.service.catalog, before)
        self.assertEqual(calls[0][1]["project_roots"], [str(self.project)])
        self.assertEqual(calls[0][1]["project"], str(self.project))
        self.assertEqual(calls[0][1]["platform"], "hermes")
        self.assertEqual(calls[0][1]["profile"], "work")

    def test_display_grouping_never_changes_path_candidates(self):
        valid = self.instructions[0]
        self.instructions += [
            dict(valid, root=".codex", proj="skills"),
            dict(valid, sub="../deep"), dict(valid, sub="unrelated"),
            dict(valid, proj="(根目录)"), dict(valid, proj="other-project"),
            dict(valid, scope="user"),
        ]
        calls = []
        def resolver(files, **kwargs):
            calls.append(kwargs)
            return {"files": []}
        with patch.dict(sys.modules, {"atlas.memory_scope": types.SimpleNamespace(build_memory_view=resolver)}):
            self.service.memories()
        self.assertEqual(calls[0]["project_roots"], [str(self.project)])

    def test_legacy_snapshot_maps_claude_memories_for_both_report_layouts(self):
        projects = [self.root / 'Documents/Code/standalone',
                    self.root / 'Documents/Codex/repo']
        self.instructions = []
        memories = []
        for project, group, sub in [(projects[0], '(Code 根目录)', ''),
                                    (projects[1], 'Codex', 'repo')]:
            project.mkdir(parents=True)
            instruction = project / 'AGENTS.md'
            instruction.write_text('fixture instructions')
            self.instructions.append({'path': str(instruction), 'scope': 'project',
                                      'root': 'display only', 'proj': group, 'sub': sub})
            encoded = re.sub(r'[^A-Za-z0-9]', '-', str(project))
            memory = self.root / '.claude/projects' / encoded / 'memory/MEMORY.md'
            memory.parent.mkdir(parents=True)
            memory.write_text('fixture memory')
            memories.append({'path': str(memory), 'category': 'memory', 'platform': 'claude',
                             'profile': 'default', 'searchable': True})
        self.service.catalog = {'files': memories}  # pre-candidate index format
        result = self.service.memories()
        self.assertEqual({row['semantics']['level'] for row in result['files']}, {'project'})
        self.assertEqual({root for row in result['files'] for root in row['semantics']['appliesTo']},
                         set(map(str, projects)))
        self.assertTrue(all(row['semantics']['observation']['state'] == 'unverified'
                            for row in result['files']))

    def test_new_snapshot_candidates_do_not_depend_on_another_instruction_load(self):
        from atlas.project_identity import project_candidates
        self.service.catalog['projectCandidates'] = project_candidates(self.instructions, home=self.root)
        self.service.catalog['home'] = str(self.root)
        def no_reload():
            raise AssertionError('Memory view mixed a newer instruction snapshot')
        self.service.instruction_loader = no_reload
        calls = []
        def resolver(files, **kwargs):
            calls.append(kwargs)
            return {'files': []}
        with patch.dict(sys.modules, {'atlas.memory_scope': types.SimpleNamespace(build_memory_view=resolver)}):
            result = self.service.memories()
        self.assertEqual(calls[0]['project_roots'], [str(self.project)])
        self.assertEqual(result['projectCandidates'], self.service.catalog['projectCandidates'])

    def test_unmatched_claude_memory_stays_unknown_after_legacy_upgrade(self):
        path = self.root / '.claude/projects/unmatched-project/memory/MEMORY.md'
        self.service.catalog['files'] = [{'path': str(path), 'category': 'memory',
                                          'platform': 'claude', 'searchable': False}]
        result = self.service.memories()
        self.assertEqual(result['files'][0]['semantics']['level'], 'unknown')
        self.assertEqual(result['files'][0]['semantics']['appliesTo'], [])

    def test_metadata_and_local_declarations_reach_view_without_becoming_scope(self):
        note = self.root / 'note.md'
        note.write_text('# Readable title\nDiscuss ' + str(self.project) + '/src/api.py')
        entry = {'path': str(note), 'name': note.name, 'category': 'memory',
                 'platform': 'other', 'profile': 'default', 'searchable': True}
        self.service.catalog['files'] = [entry]
        calls = []
        def resolver(files, **kwargs):
            calls.append(kwargs)
            return {'files': [dict(entry, semantics={'appliesTo': [], 'level': 'unknown'})]}
        with patch.dict(sys.modules, {'atlas.memory_scope': types.SimpleNamespace(build_memory_view=resolver)}):
            result = self.service.memories()
        info = result['files'][0].get('memoryInfo', {})
        self.assertEqual(info.get('title'), 'Readable title')
        self.assertEqual(info['relatedProjects'][0]['path'], str(self.project))
        self.assertEqual(result['files'][0]['semantics']['appliesTo'], [])
        self.assertEqual(calls[0]['extension_evidence'], {})
        self.assertNotIn('content', info)
        self.assertNotIn('memoryInfo', self.service.catalog['files'][0])

    def test_storage_tree_uses_only_the_filtered_memory_inventory(self):
        entry = {'path': str(self.root / 'memories/topic.md'), 'category': 'memory'}
        self.service.catalog['files'] = [entry, {'path': str(self.root / 'elsewhere/hidden.md'), 'category': 'memory'}]
        def resolver(files, **kwargs):
            return {'files': [dict(entry, semantics={'level': 'unknown'})]}
        with patch.dict(sys.modules, {'atlas.memory_scope': types.SimpleNamespace(build_memory_view=resolver)}):
            result = self.service.memories(platform='fixture')
        self.assertIn('storageTree', result)
        self.assertEqual(result['storageTree']['totalFiles'], 1)
        self.assertEqual(result['files'][0]['memoryStorage']['directory'], str(self.root / 'memories'))
        self.assertNotIn('hidden.md', str(result['storageTree']))


if __name__ == "__main__":
    unittest.main()
