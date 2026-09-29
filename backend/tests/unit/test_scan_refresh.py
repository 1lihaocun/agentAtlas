
from pathlib import Path
import tempfile
import unittest

from atlas.instructions import scanner as scan


class ScanRefreshTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[3] / '.agentatlas/work')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.root = self.home / 'projects'
        self.root.mkdir()

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
        data = scan.scan([str(child.parent), str(self.root), str(child.parent)], home=self.home, max_depth=2)
        self.assertEqual({f['path'] for f in data['files']}, {str(child), str(deeper), str(peer)})
        self.assertEqual(len(data['files']), 3)

    def test_parent_then_child_retains_the_childs_full_depth_budget(self):
        shallow = self.put('repo/AGENTS.md')
        deep = self.put('repo/a/b/CLAUDE.md', '# Independent depth\n')
        self.put('repo/a/b/outside/AGENTS.md')
        roots = [str(self.root), str(shallow.parent)]
        for ordering in (roots, list(reversed(roots))):
            with self.subTest(order=ordering):
                data = scan.scan(ordering, home=self.home, max_depth=2)
                self.assertEqual({f['path'] for f in data['files']}, {str(shallow), str(deep)})
        # 两次独立调用都应覆盖真实文件，不能因默认 HOME 裁剪而空集相等。
        for _ in range(2):
            self.assertEqual(sorted(scan.walk_root(str(shallow.parent), 2, home=self.home)),
                             sorted([str(shallow), str(deep)]))

    def test_openclaw_outside_home_has_unknown_semantics(self):
        from atlas.memories.scope import build_memory_view
        for path in (self.home, self.home.parent / 'outside.md'):
            data = build_memory_view([{'path': str(path), 'category': 'memory',
                                       'platform': 'openclaw'}], home=self.home)
            self.assertEqual(data['files'][0]['semantics']['level'], 'unknown')

    def test_unsupported_rule_roots_are_not_advertised(self):
        from atlas.platforms import scan_root_names
        roots = scan_root_names()
        self.assertNotIn('.codex/.tmp/plugins', roots)
        self.assertNotIn('.continue/rules', roots)

    def test_instruction_names_have_one_explicit_owner(self):
        from atlas.platforms import instruction_platform, platforms
        owners = {}
        for spec in platforms().values():
            for name in spec.instruction_names:
                self.assertNotIn(name.lower(), owners)
                owners[name.lower()] = spec.id
        self.assertEqual(instruction_platform()['agents.md'], 'shared')

    def test_code_root_instruction_is_not_a_filename_group(self):
        path = self.home / 'Documents/Code/AGENTS.md'
        path.parent.mkdir(parents=True)
        path.write_text('# 根目录指令\n', encoding='utf-8')
        data = scan.scan([str(path.parent)], home=self.home)
        self.assertEqual(data['files'][0]['root'], '(Code 根目录)')
        self.assertEqual(data['roots'][0]['name'], '(Code 根目录)')

    def test_repeated_scan_does_not_reuse_old_duplicate_groups(self):
        first = self.put('a/AGENTS.md')
        second = self.put('b/CLAUDE.md')
        before = scan.scan([str(self.root)], home=self.home)
        self.assertEqual(before['dupFiles'], 2)
        self.assertEqual(before['dupGroups'], [[str(first), str(second)]])
        second.unlink()
        after = scan.scan([str(self.root)], home=self.home)
        self.assertEqual(after['totalFiles'], 1)
        self.assertEqual(after['dupGroups'], [], 'Deleted paths cannot survive in the next scan')
        self.assertEqual(after['dupFiles'], 0)
        self.assertEqual(after['uniqueFiles'], 1)
        self.assertFalse(after['files'][0]['dup'])
        again = scan.scan([str(self.root)], home=self.home)
        for key in ('files', 'roots', 'dupGroups', 'projectCandidates', 'uniqueFiles'):
            self.assertEqual(after[key], again[key])


if __name__ == '__main__':
    unittest.main()
