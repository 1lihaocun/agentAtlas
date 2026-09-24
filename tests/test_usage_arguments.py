"""Tool argument decoding must not turn uncertain evidence into zero usage."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest

from atlas.usage import compute_usage
from atlas.retirement import build_retirement
from atlas.storage import Store


class UsageArgumentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.home() / '.hermes/cache/scratch')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.logs = self.root / 'logs'
        self.home = self.root / 'codex'
        self.cwd = self.root / 'elsewhere'
        for directory in (self.logs, self.home, self.cwd):
            directory.mkdir()
        self.target = self.root / 'a space 中文' / 'AGENTS.md'
        self.target.parent.mkdir()
        self.target.write_text('## Rules\nsynthetic\n')
        self.now = 2_000_000_000
        os.utime(self.target, (self.now - 100 * 86400,) * 2)
        self.index = {'files':[{'path':str(self.target), 'scope':'project'}]}
        self.store = Store(self.root / 'state')

    def collect(self, payload):
        timestamp = datetime.fromtimestamp(self.now - 60, timezone.utc).isoformat()
        events = [
            {'type':'session_meta', 'timestamp':timestamp, 'payload':{'id':'fixture','cwd':str(self.cwd)}},
            {'type':'response_item', 'timestamp':timestamp, 'payload':payload},
        ]
        (self.logs / 'fixture.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
        data = compute_usage(self.index, self.logs, self.home, now=self.now, use_cache=False)
        queue = build_retirement(self.index, data, self.store, self.now)
        return data, queue['rows'][0]

    def test_json_arguments_and_direct_text_produce_the_same_mention(self):
        command = 'cat "' + str(self.target) + '"'
        payloads = [
            {'text':command},
            {'type':'function_call','name':'shell','arguments':json.dumps({'command':command})},
            {'type':'function_call','name':'shell','arguments':json.dumps({'command':['cat', str(self.target)]})},
            {'type':'function_call','name':'read_file','arguments':json.dumps({'path':str(self.target)})},
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                data, row = self.collect(payload)
                self.assertEqual(data['sourceStatus'], 'available')
                self.assertEqual(row['mentions'], 1)
                self.assertEqual(row['estimatedSessions'], 0)
                self.assertIsNone(row['confirmedReads'])
                self.assertEqual(row['bucket'], 'unknown', 'Mentioned file must not become a zero-signal candidate')
                self.assertEqual(row['reason'], 'mention_without_read_confirmation')
                self.assertNotIn(command, json.dumps(data), 'Do not return raw commands')

    def test_malformed_or_unexpected_arguments_make_zero_estimate_unknown(self):
        for arguments in ('{"command":', 'null', '42', json.dumps('not an argument object')):
            with self.subTest(arguments=arguments):
                data, row = self.collect({'type':'function_call','name':'shell','arguments':arguments})
                self.assertEqual(data['sourceStatus'], 'partial')
                self.assertIn('invalid_tool_arguments', data['warnings'])
                self.assertIsNone(row['estimatedSessions'])
                self.assertEqual(row['bucket'], 'unknown')

    def test_oversized_or_excessively_nested_arguments_are_bounded_and_unknown(self):
        arguments = [json.dumps({'command':'x' * 70000}), '[' * 1500 + '0' + ']' * 1500]
        for value in arguments:
            with self.subTest(size=len(value)):
                data, row = self.collect({'type':'function_call','name':'shell','arguments':value})
                self.assertEqual(data['sourceStatus'], 'partial')
                self.assertIsNone(row['estimatedSessions'])
                self.assertEqual(row['bucket'], 'unknown')


if __name__ == '__main__':
    unittest.main()
