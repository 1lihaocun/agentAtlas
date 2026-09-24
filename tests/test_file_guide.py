"""File descriptions interpret metadata, never change runtime scope or files."""
import importlib
from pathlib import Path
import unittest


class FileGuideTests(unittest.TestCase):
    def guide(self):
        return importlib.import_module('atlas.file_guide').FileGuide()

    def test_activity_filename_has_explained_fields_not_guessed_hash_or_timezone(self):
        guide = self.guide()
        name = '2026-09-16T00-00-00-VRTL-6h-memory-summary.md'
        entry = {'path': '/home/test/.codex/memories/extensions/skysight/resources/' + name,
                 'category': 'memory', 'platform': 'codex', 'profile': 'default'}
        result = guide.describe(entry, home='/home/test')
        self.assertEqual(result['id'], 'codex.skysight.resource')
        self.assertEqual(result['role'], '记忆素材')
        self.assertEqual(result['filename']['status'], 'matched')
        self.assertEqual(result['filename']['original'], name)
        values = {field['key']:field['value'] for field in result['filename']['fields']}
        self.assertEqual(values, {'timestamp':'2026-09-16T00-00-00','identifier':'VRTL',
                                 'window':'6h','slug':'memory-summary','extension':'.md'})
        self.assertTrue(result['sources'])
        self.assertIn('未', str(result['filename']['cautions']))
        self.assertNotIn('appliesTo',result)
        self.assertNotIn('editable',result)
        self.assertNotIn('semantics',entry)

    def test_library_covers_categories_without_guessing_platform_specific_rules(self):
        from atlas.catalog import CATEGORIES
        guide = self.guide()
        book = guide.library()
        self.assertEqual(book['schemaVersion'], 1)
        self.assertEqual(len({t['id'] for t in book['types']}), len(book['types']))
        for category in CATEGORIES:
            with self.subTest(category=category):
                result = guide.describe({'path':'/work/file.dat','category':category,'platform':'unrecognized'}, home='/home/test')
                self.assertNotEqual(result['id'],'unknown')
                self.assertEqual(result['evidence'],'清单分类，非平台契约')
                self.assertEqual(result['filename']['status'],'unknown')
        wrong = guide.describe({'path':'/work/2026-09-16T00-00-00-VRTL-6h-memory-summary.md', 'category':'memory','platform':'codex'}, home='/home/test')
        self.assertEqual(wrong['id'],'category.memory')
        nested = guide.describe({'path':'/home/test/.codex/memories/extensions/skysight/resources/nested/2026-09-16T00-00-00-VRTL-6h-memory-summary.md','category':'memory','platform':'codex'},home='/home/test')
        self.assertEqual(nested['id'],'category.memory')

    def test_invalid_dates_and_noncanonical_paths_are_not_repaired(self):
        guide = self.guide()
        base='/home/test/.codex/memories/extensions/skysight/resources/'
        for name in ['2026-02-30T00-00-00-VRTL-6h-memory-summary.md','2026-09-16T25-00-00-VRTL-6h-memory-summary.md','2026-09-16T00-00-00-VRTL-9h-memory-summary.md']:
            result=guide.describe({'path':base+name,'category':'memory','platform':'codex'},home='/home/test')
            self.assertEqual(result['filename']['original'],name)
            self.assertEqual(result['filename']['status'],'unknown')
            self.assertEqual(result['filename']['fields'],[])
        for path in [base+'../x.md',base+'./x.md','relative/file.md']:
            result=guide.describe({'path':path,'category':'memory','platform':'codex'},home='/home/test')
            self.assertEqual(result['id'],'unknown')

    def test_hermes_proposal_uses_verified_short_uuid_not_a_timestamp(self):
        entry = {'path': '/home/test/.hermes/pending/memory/1a2b3c4d.json', 'category': 'memory', 'platform': 'hermes'}
        result = self.guide().describe(entry, home='/home/test')
        self.assertEqual(result['id'], 'hermes.memory.proposal')
        self.assertEqual(result['filename']['status'], 'matched')
        fields = {field['key']: field for field in result['filename']['fields']}
        self.assertEqual(fields['identifier']['value'], '1a2b3c4d')
        self.assertIn('UUID', fields['identifier']['meaning'])
        invalid = dict(entry, path=entry['path'].replace('1a2b3c4d', '1A2B3C4D'))
        self.assertEqual(self.guide().describe(invalid, home='/home/test')['filename']['status'], 'unknown')

    def test_skysight_reader_utc_and_fixed_suffix_are_not_generalized_to_chronicle(self):
        base = '/home/test/.codex/memories/extensions/'
        name = '2026-09-16T00-10-00-CHvw-10min-memory-summary.md'
        entry = {'path': base + 'skysight/resources/' + name, 'category': 'memory', 'platform': 'codex'}
        result = self.guide().describe(entry, home='/home/test')
        timestamp = next(f for f in result['filename']['fields'] if f['key'] == 'timestamp')
        self.assertIn('UTC', timestamp['meaning'])
        self.assertTrue(any(s.get('version') == '26.917.51856' for s in result['sources']))
        renamed = dict(entry, path=entry['path'].replace('memory-summary.md', 'topic.md'))
        self.assertEqual(self.guide().describe(renamed, home='/home/test')['filename']['status'], 'unknown')
        chronicle = dict(renamed, path=renamed['path'].replace('/skysight/', '/chronicle/'))
        parsed = self.guide().describe(chronicle, home='/home/test')['filename']
        self.assertEqual(parsed['status'], 'matched')
        self.assertNotIn('UTC', next(f for f in parsed['fields'] if f['key'] == 'timestamp')['meaning'])

    def test_registry_edits_reload_and_invalid_versions_do_not_poison_recovery(self):
        import json
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'guide.json'
            data=json.loads(self.guide().path.read_text())
            path.write_text(json.dumps(data))
            guide=importlib.import_module('atlas.file_guide').FileGuide(path)
            original=guide.library()['types'][0]['label']
            data['types'][0]['label']='Updated type label'
            path.write_text(json.dumps(data))
            self.assertEqual(guide.library()['types'][0]['label'],'Updated type label')
            data['types'].append(dict(data['types'][0]))
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError): guide.library()
            data['types'].pop();data['types'][0]['label']=original;path.write_text(json.dumps(data))
            self.assertEqual(guide.library()['types'][0]['label'],original)
            # Classification reads the registry, never the described source file.
            with patch('builtins.open',side_effect=AssertionError('must not read source')):
                result=guide.describe({'path':'/not/read/session.jsonl','platform':'codex','category':'session'})
            self.assertEqual(result['id'],'category.session')


if __name__ == '__main__':
    unittest.main()
