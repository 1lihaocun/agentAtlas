"""Instruction display contracts are separate from path identity candidates."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atlas import scan


class ScanIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path.home() / '.hermes/cache/scratch')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        home_patch = patch.object(scan, 'HOME', str(self.home))
        home_patch.start()
        self.addCleanup(home_patch.stop)

    def put(self, relative):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture instruction\n')
        return path

    def test_scan_exposes_identity_candidates_without_changing_display_groups(self):
        first = self.put('Documents/Code/standalone/AGENTS.md')
        child = self.put('Documents/Code/standalone/a/AGENTS.md')
        second = self.put('Documents/Codex/repo/AGENTS.md')
        with patch.object(scan, 'ROOTS', [str(self.home / 'Documents')]):
            data = scan.scan()
        rows = {row['path']: row for row in data['files']}
        self.assertEqual(rows[str(first)]['proj'], '(Code 根目录)')
        self.assertEqual(rows[str(second)]['proj'], 'Codex')
        self.assertEqual(rows[str(second)]['sub'], 'repo')
        self.assertEqual({row['path'] for row in data.get('projectCandidates', [])},
                         {str(first.parent), str(second.parent)})
        self.assertEqual(rows[str(child)].get('projectPath'), str(first.parent))

    def test_map_identity_uses_local_platform_workspace_not_ancestor_instruction(self):
        shared = self.put('work/collection/CLAUDE.md')
        child = self.put('work/collection/app/selftest/deep/CLAUDE.md')
        self.put('work/collection/app/.hermes/skills/demo/SKILL.md')
        with patch.object(scan, 'ROOTS', [str(shared.parent)]):
            data = scan.scan()
        entry = next(row for row in data['files'] if row['path'] == str(child))
        self.assertEqual(entry['projectPath'], str(self.home / 'work/collection/app'))

    def test_configured_opencode_user_location_is_only_a_scope_candidate(self):
        path = self.put('.config/opencode/AGENTS.md')
        row = scan.file_meta(str(path))
        self.assertEqual(row['scope'], 'user')
        self.assertEqual(row.get('scopeBasis', {}).get('kind'), 'known_user_location')
        self.assertEqual(row.get('scopeBasis', {}).get('observation'), 'unverified')
        project = self.put('repo/.config/opencode/AGENTS.md')
        self.assertEqual(scan.file_meta(str(project))['scope'], 'project')


if __name__ == '__main__':
    unittest.main()
