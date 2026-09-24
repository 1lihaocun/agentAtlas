"""Storage topology never infers runtime memory applicability."""
import importlib
import unittest


class MemoryStorageTests(unittest.TestCase):
    def build(self, files):
        module = importlib.import_module('atlas.memory_storage')
        return module.build_memory_storage(files, home='/home/test')

    def asset(self, path, **kwargs):
        return dict(path=path, category='memory', **kwargs)

    def test_real_directories_include_empty_intermediates_and_exact_subtree_counts(self):
        base = '/home/test/.codex/memories'
        paths = [base+'/MEMORY.md', base+'/extensions/skysight/instructions.md',
                 base+'/extensions/skysight/resources/day.md', base+'/extensions/ad_hoc/notes/note.md']
        tree = self.build([self.asset(p) for p in paths])
        self.assertEqual(tree['schemaVersion'], 1)
        self.assertEqual(tree['roots'], [base])
        root = tree['nodes'][base]
        self.assertEqual(root['displayPath'], '~/.codex/memories')
        self.assertEqual((root['directCount'], root['totalCount']), (1, 4))
        self.assertEqual(root['children'], [base+'/extensions'])
        extension = tree['nodes'][base+'/extensions']
        self.assertEqual((extension['directCount'], extension['totalCount']), (0, 3))
        leaf = base+'/extensions/skysight/resources'
        self.assertEqual(tree['nodes'][leaf]['directFiles'], [leaf+'/day.md'])
        self.assertEqual(tree['locations'][leaf+'/day.md']['directory'], leaf)
        self.assertEqual(tree['locations'][leaf+'/day.md']['ancestors'],
                         [base,base+'/extensions',base+'/extensions/skysight',leaf])
        self.assertEqual(tree['totalFiles'], 4)
        self.assertEqual(tree['unplacedFiles'], [])
        self.assertNotIn('appliesTo', str(tree))

    def test_roots_counts_and_nonmemory_exclusions_are_a_closed_inventory(self):
        import copy
        from unittest.mock import patch
        files = [self.asset('/home/test/.hermes/memories/MEMORY.md'),
                 self.asset('/home/test/.hermes/pending/memory/proposal.json'),
                 self.asset('/home/test/.openclaw/memory/main.sqlite', searchable=False),
                 self.asset('/home/test/.codex/memories/extensions/a/notes/a.md'),
                 self.asset('/home/test/.codex/memories/extensions/ab/notes/b.md'),
                 {'path': '/home/test/.codex/AGENTS.md', 'category': 'instruction'},
                 self.asset('relative.md'), self.asset('/home/test/x/../other.md')]
        files.append(dict(files[0]))
        before = copy.deepcopy(files)
        with patch('builtins.open', side_effect=AssertionError('pure topology must not open files')):
            tree = self.build(files)
        self.assertEqual(files, before)
        self.assertEqual(tree['totalFiles'], 7)
        self.assertEqual(sum(tree['nodes'][r]['totalCount'] for r in tree['roots']) + len(tree['unplacedFiles']), 7)
        self.assertEqual(len(tree['locations']), 5)
        self.assertIn('/home/test/.openclaw/memory/main.sqlite', tree['locations'])
        for node in tree['nodes'].values():
            self.assertEqual(node['totalCount'], node['directCount'] + sum(tree['nodes'][c]['totalCount'] for c in node['children']))
        self.assertEqual(tree['nodes']['/home/test/.codex/memories/extensions/a']['totalCount'], 1)
        self.assertEqual(set(tree['unplacedFiles']), {'relative.md','/home/test/x/../other.md'})

    def test_filtering_retains_known_ancestor_directories_without_excluded_files(self):
        module = importlib.import_module('atlas.memory_storage')
        base = '/home/test/.codex/memories'
        source = base + '/extensions/a/notes/one.md'
        files = [self.asset(base+'/MEMORY.md'), self.asset(source), self.asset(base+'/rollouts/other.md')]
        tree = module.build_memory_storage(files, home='/home/test', selected_paths={source})
        self.assertEqual(tree['roots'], [base])
        self.assertEqual(tree['nodes'][base]['totalCount'], 1)
        self.assertEqual(tree['nodes'][base]['directCount'], 0)
        self.assertEqual(tree['nodes'][base]['filePaths'], [source])
        self.assertNotIn(base+'/MEMORY.md', tree['locations'])
        self.assertNotIn(base+'/rollouts', tree['nodes'])
        self.assertEqual(tree['totalFiles'], 1)

    def test_empty_home_file_and_external_roots_stay_addressable(self):
        self.assertEqual(self.build([])['totalFiles'], 0)
        tree = self.build([self.asset('/home/test/MEMORY.md'),self.asset('/home/test/.hermes/memories/USER.md')])
        self.assertEqual(tree['roots'], ['/home/test'])
        self.assertEqual(tree['nodes']['/home/test']['displayPath'], '~')
        external = self.build([self.asset('/opt/work/memory.md'),self.asset('/home/test/.hermes/memories/USER.md')])
        self.assertEqual(len(external['roots']), 2)
        self.assertEqual(external['locations']['/opt/work/memory.md']['directory'], '/opt/work')


if __name__ == '__main__':
    unittest.main()
