
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from atlas.guides.provenance import ProvenanceService, _note_stamp, _utc
from atlas.guides.service import FileGuide

SCRATCH = Path(__file__).resolve().parents[3] / '.agentatlas/work'


def _make_db(path, jobs=True, stage1=True):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE _sqlx_migrations (version TEXT)")
    if jobs:
        con.execute("CREATE TABLE jobs (kind TEXT, job_key TEXT, status TEXT, worker_id TEXT,"
                    " ownership_token TEXT, started_at INT, finished_at INT, lease_until INT,"
                    " retry_at INT, retry_remaining INT, last_error TEXT, input_watermark INT,"
                    " last_success_watermark INT)")
        con.execute("INSERT INTO jobs (kind, job_key, status, started_at, finished_at, retry_remaining)"
                    " VALUES ('memory_stage1', 'tid1', 'done', 1788403060, 1788403100, 3)")
        con.execute("INSERT INTO jobs (kind, job_key, status, started_at, finished_at, retry_remaining)"
                    " VALUES ('memory_consolidate_global', 'global', 'done', 1788403061, 1788403200, 0)")
    if stage1:
        con.execute("CREATE TABLE stage1_outputs (thread_id TEXT, source_updated_at INT,"
                    " raw_memory TEXT, rollout_summary TEXT, rollout_slug TEXT, generated_at INT,"
                    " usage_count INT, last_usage INT, selected_for_phase2 INT,"
                    " selected_for_phase2_source_updated_at INT)")
        con.execute("INSERT INTO stage1_outputs (rollout_slug, generated_at, usage_count, last_usage,"
                    " selected_for_phase2) VALUES ('rustfs-upload-502', 1788403060, 5, 1788500000, 1)")
    con.commit()
    con.close()


class ProvenanceMechanismTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(temporary.cleanup)
        self.codex_home = Path(temporary.name)

    def service(self, **kwargs):
        kwargs.setdefault('home', '/home/test')
        kwargs.setdefault('codex_home', str(self.codex_home))
        return ProvenanceService(guide=FileGuide(), **kwargs)

    def test_official_docs_are_attached_with_https_links(self):
        service = self.service()
        codex = service.describe({'path': '/home/test/.codex/memories/MEMORY.md',
                                  'category': 'memory', 'platform': 'codex'})
        self.assertTrue(codex['docs'])
        for doc in codex['docs']:
            self.assertTrue(doc['url'].startswith('https://'), doc)
        hermes = service.describe({'path': '/home/test/.hermes/memories/MEMORY.md',
                                   'category': 'memory', 'platform': 'hermes'})
        self.assertTrue(hermes['docs'])
        self.assertTrue(hermes['docs'][0]['url'].startswith('https://'))
        self.assertEqual(hermes['mechanism'], '模型在会话中主动写入')

    def test_type_library_carries_mechanism_and_doc_urls(self):
        book = FileGuide().library()
        by_id = {t['id']: t for t in book['types']}
        self.assertIn('generationMechanism', by_id['codex.rollout.summary'])
        self.assertIn('docs', by_id['hermes.memory'])
        self.assertTrue(by_id['hermes.memory']['docs'][0].startswith('https://'))
        self.assertNotIn('generationMechanism', by_id['category.other'])

    def test_codex_summary_reads_jobs_readonly_without_source_file(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as temp:
            _make_db(os.path.join(temp, 'memories_1.sqlite'))
            service = self.service(codex_home=temp)
            result = service.describe({'path': '/home/test/.codex/memories/MEMORY.md',
                                       'category': 'memory', 'platform': 'codex'})
            self.assertTrue(result['traced'])
            kinds = [record['kind'] for record in result['evidence'][0]['records']]
            self.assertIn('memory_stage1', kinds)
            self.assertIn('memory_consolidate_global', kinds)
            self.assertTrue(result['docs'])

    def test_rollout_summary_matches_stage1_slug_and_usage(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as temp:
            _make_db(os.path.join(temp, 'memories_1.sqlite'))
            service = self.service(codex_home=temp)
            result = service.describe({'path': '/home/test/.codex/memories/rollout_summaries/rustfs-upload-502.md',
                                       'category': 'memory', 'platform': 'codex'})
            blocks = [e for e in result['evidence'] if e['kind'] == '产物对照']
            self.assertEqual(len(blocks), 1)
            self.assertEqual(blocks[0]['records'][0]['usageCount'], 5)
            self.assertTrue(blocks[0]['records'][0]['selectedForPhase2'])

    def test_sqlite_uri_keeps_literal_percent_query_and_fragment(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as temp:
            root = Path(temp) / 'my%20dir?#'
            root.mkdir()
            _make_db(root / 'memories_1.sqlite')
            service = self.service(codex_home=str(root))
            self.assertTrue(service._codex_job_evidence('MEMORY.md')['traced'])
            self.assertEqual(service._stage1_match('rustfs-upload-502.md')[0]['usageCount'], 5)

    def test_missing_db_reports_absence_not_error(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as temp:
            result = self.service(codex_home=temp).describe(
                {'path': '/home/test/.codex/memories/MEMORY.md', 'category': 'memory', 'platform': 'codex'})
            self.assertFalse(result['traced'])
            self.assertIn('memories_1.sqlite', result['note'])

    def test_adhoc_note_traces_writing_tool_call_in_session_log(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as temp:
            note_dir = os.path.join(temp, '.codex', 'memories', 'extensions', 'ad_hoc', 'notes')
            os.makedirs(note_dir)
            name = '20260906T122400Z-demo-note.md'
            session_dir = os.path.join(temp, '.codex', 'sessions', '2026', '09', '06')
            os.makedirs(session_dir)
            session = os.path.join(session_dir, 'rollout-1.jsonl')
            patch_text = 'text(await tools.apply_patch("*** Begin Patch\\n*** Add File: ' + note_dir + '/' + name + '\\n+hello"'
            call = {'timestamp': '2026-09-06T12:24:00.000Z', 'ordinal': 5, 'type': 'response_item',
                    'payload': {'type': 'custom_tool_call', 'name': 'exec', 'call_id': 'c1',
                                'arguments': patch_text}}
            out = {'timestamp': '2026-09-06T12:24:01.000Z', 'ordinal': 6, 'type': 'response_item',
                   'payload': {'type': 'custom_tool_call_output', 'call_id': 'c1', 'output': 'patch applied'}}
            with open(session, 'w', encoding='utf-8') as stream:
                stream.write(json.dumps(call) + '\n' + json.dumps(out) + '\n')
            service = self.service(home=temp, codex_home=os.path.join(temp, '.codex'))
            result = service.describe({'path': note_dir + '/' + name,
                                       'category': 'memory', 'platform': 'codex'})
            self.assertTrue(result['traced'])
            hits = [e for e in result['evidence'] if e['kind'] == '写入调用']
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]['tool'], 'exec')
            self.assertIn('apply_patch', hits[0]['input'])
            self.assertEqual(hits[0]['output'], 'patch applied')
            # The rule text always explains the mechanism even without a trace.
            self.assertTrue(any(e['kind'] == '机制说明' for e in result['evidence']))

    def test_adhoc_trace_failure_is_honest_not_fabricated(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as temp:
            note_dir = os.path.join(temp, '.codex', 'memories', 'extensions', 'ad_hoc', 'notes')
            os.makedirs(note_dir)
            result = self.service(home=temp, codex_home=os.path.join(temp, '.codex')).describe(
                {'path': note_dir + '/20260906T122400Z-gone.md',
                 'category': 'memory', 'platform': 'codex'})
            self.assertFalse(result['traced'])
            self.assertIn('未在本地会话记录中找到', result['note'])
            self.assertFalse(any(e['kind'] == '写入调用' for e in result['evidence']))

    def test_extension_resources_point_to_local_instructions(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as temp:
            ext = os.path.join(temp, '.codex', 'memories', 'extensions', 'skysight')
            os.makedirs(ext)
            with open(os.path.join(ext, 'instructions.md'), 'w', encoding='utf-8') as stream:
                stream.write('# Skysight Memory Instructions\n')
            result = self.service(home=temp, codex_home=os.path.join(temp, '.codex')).describe(
                {'path': ext + '/resources/2026-09-16T00-10-00-ABcd-10min-memory-summary.md',
                 'category': 'memory', 'platform': 'codex'})
            self.assertTrue(result['traced'])
            declared = [e for e in result['evidence'] if e['kind'] == '扩展声明']
            self.assertEqual(len(declared), 1)
            self.assertTrue(declared[0]['path'].endswith('skysight/instructions.md'))

    def test_hermes_proposal_explains_write_approval(self):
        result = self.service().describe({'path': '/home/test/.hermes/pending/memory/1a2b3c4d.json',
                                          'category': 'memory', 'platform': 'hermes'})
        self.assertFalse(result['traced'])
        self.assertIn('write_approval', result['evidence'][0]['detail'])
        self.assertTrue(result['docs'])

    def test_memory_skill_type_mechanism_and_provenance(self):
        # 类型库:skills 分类 + 机制 + 加载方式
        book = FileGuide().library()
        by_id = {t['id']: t for t in book['types']}
        self.assertIn('codex.memory.skill', by_id)
        skill = by_id['codex.memory.skill']
        self.assertIn('后台整理任务', skill['generationMechanism'])
        self.assertIn('不自动注入', skill['loadingMechanism'])
        # 匹配:SKILL.md 路径命中,scripts 命中,其他目录不误配
        index = self.codex_home / 'memories/MEMORY.md'
        index.parent.mkdir()
        index.write_text('Related skill: skills/demo-publish/SKILL.md\n', encoding='utf-8')
        for category in ('memory', 'skill'):  # 真实清单是 skill 分类,也要命中
            hit = self.service().describe({'path': '/home/test/.codex/memories/skills/demo-publish/SKILL.md',
                                           'category': category, 'platform': 'codex'})
            self.assertEqual(hit['typeId'], 'codex.memory.skill', category)
        self.assertEqual(hit['typeId'], 'codex.memory.skill')
        # provenance:机制说明 + MEMORY.md 指针对照(有指针文件时)
        self.assertTrue(hit['traced'])
        kinds = [e['kind'] for e in hit['evidence']]
        self.assertIn('机制说明', kinds)
        self.assertIn('指针对照', kinds)

    def test_unknown_type_stays_conservative(self):
        result = self.service().describe({'path': '/work/other.txt',
                                          'category': 'other', 'platform': 'unrecognized'})
        self.assertFalse(result['traced'])
        self.assertIn('暂无', result['note'])
        self.assertEqual(result['docs'], [])

    def test_oversized_session_line_is_skipped_without_losing_next_record(self):
        from atlas.guides.provenance import MAX_SESSION_LINE_BYTES
        with tempfile.TemporaryDirectory(dir=SCRATCH) as temp:
            session = Path(temp) / 'rollout.jsonl'
            name = '20260906T122400Z-demo.md'
            call = {'payload': {'type': 'custom_tool_call', 'name': 'apply_patch',
                    'call_id': 'write', 'input': '*** Add File: ' + name}}
            session.write_bytes(b'x' * (MAX_SESSION_LINE_BYTES + 128) + b'\n'
                                + json.dumps(call).encode('utf-8') + b'\n')
            hits = self.service(codex_home=temp)._scan_session_for_note(str(session), name)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]['tool'], 'apply_patch')
            self.assertEqual(hits[0]['line'], 1)

    def test_session_roots_share_one_scan_budget(self):
        from atlas.guides.provenance import MAX_SESSION_SCAN_BYTES
        with tempfile.TemporaryDirectory(dir=SCRATCH) as temp:
            for folder in ('sessions', 'archived_sessions'):
                day = Path(temp) / folder / '2026/09/06'
                day.mkdir(parents=True)
                with (day / 'rollout.jsonl').open('wb') as stream:
                    stream.truncate(MAX_SESSION_SCAN_BYTES // 2 + 1)
            service = self.service(codex_home=temp)
            with self.assertRaisesRegex(ValueError, '字节预算'):
                service._adhoc_trace('20260906T122400Z-demo.md', {})

    def test_session_scan_enforces_shared_bytes_and_deadline(self):
        from atlas.guides.provenance import SessionScanBudget
        with tempfile.TemporaryDirectory(dir=SCRATCH) as temp:
            service = self.service(codex_home=temp)
            session = Path(temp) / 'rollout.jsonl'
            session.write_text('{}\n', encoding='utf-8')
            budget = SessionScanBudget(max_bytes=5)
            self.assertEqual(service._scan_session_for_note(str(session), 'note.md', budget), [])
            with self.assertRaisesRegex(ValueError, '字节预算'):
                service._scan_session_for_note(str(session), 'note.md', budget)
            with self.assertRaisesRegex(ValueError, '时间预算'):
                service._scan_session_for_note(str(session), 'note.md', SessionScanBudget(seconds=0))

    def test_read_calls_are_not_write_evidence(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as temp:
            session = Path(temp) / 'rollout.jsonl'
            name = '20260906T122400Z-demo.md'
            for tool in ('exec', 'shell', None, 'apply_patch'):
                session.write_text(json.dumps({'payload': {'type': 'function_call',
                    'name': tool, 'call_id': 'read', 'arguments': 'cat ' + name}}) + '\n', encoding='utf-8')
                self.assertEqual(self.service(codex_home=temp)._scan_session_for_note(str(session), name), [])
            session.write_text(json.dumps({'payload': {'type': 'custom_tool_call',
                'name': 'apply_patch', 'call_id': 'write',
                'input': '*** Begin Patch\n*** Add File: ' + name + '\n+内容\n*** End Patch'}}) + '\n', encoding='utf-8')
            self.assertEqual(len(self.service(codex_home=temp)._scan_session_for_note(str(session), name)), 1)

    def test_day_candidates_use_the_note_date(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as temp:
            day = Path(temp) / '2026/09/06'
            day.mkdir(parents=True)
            session = day / 'rollout.jsonl'
            session.write_text('', encoding='utf-8')
            for name in ('20260906T122400Z-demo.md', '20260906T142858+0800-demo.md'):
                self.assertEqual(self.service(codex_home=temp)._session_candidates(
                    temp, _note_stamp(name)), [str(session)])

    def test_note_stamp_and_utc_helpers(self):
        self.assertEqual(_note_stamp('20260906T122400Z-demo.md'), '20260906T122400Z')
        self.assertEqual(_note_stamp('20260906T142858+0800-http.md'), '20260906T142858+0800')
        self.assertEqual(_note_stamp('random.md'), '')
        self.assertEqual(_utc(1788403060), '2026-09-03 02:37:40Z')  # fixed, tz-aware
        self.assertIsNone(_utc(None))
        self.assertIsNone(_utc('not-a-number'))


if __name__ == '__main__':
    unittest.main()
