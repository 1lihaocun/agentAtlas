"""External grader controls and opt-in CLI contract; no model calls in tests."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from atlas.eval.curate import CASES
from atlas.eval.runner import grade, main
from atlas.eval.codex import preflight


@unittest.skipUnless(sys.platform == 'darwin', 'macOS Seatbelt boundary required')
class EvalRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path.home()/'.hermes/cache/scratch')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.project=Path(__file__).resolve().parents[1]

    def workspace(self,name):
        root=self.root/name
        (root/'atlas').mkdir(parents=True)
        for filename in ('effective.py','usage.py'):
            shutil.copyfile(self.project/'atlas'/filename,root/'atlas'/filename)
        (root/'AGENTS.md').write_text('## Rules\nFrozen instructions.\n')
        return root

    def test_trusted_checks_accept_current_source_and_reject_each_reconstruction(self):
        for case_id,group,target,grader,before,after,task in CASES:
            with self.subTest(case=case_id):
                fixed=self.workspace(case_id+'-fixed')
                result=grade(fixed,grader,self.root/(case_id+'-positive'))
                self.assertEqual(result['score'],1,result)
                broken=self.workspace(case_id+'-broken')
                source=(broken/target).read_text()
                self.assertEqual(source.count(before),1,'mutation anchor drifted')
                (broken/target).write_text(source.replace(before,after,1))
                result=grade(broken,grader,self.root/(case_id+'-negative'))
                self.assertEqual(result['status'],'completed',result)
                self.assertEqual(result['score'],0,result)
                self.assertTrue(result['checks'],result)

    def test_cli_cannot_execute_by_default(self):
        with patch('atlas.eval.runner.run_pilot') as execute:
            with self.assertRaises(SystemExit) as raised:
                main(['pilot','missing.jsonl','--output',str(self.root/'out'),
                      '--model','not-a-real-call','--max-calls','1'])
            self.assertEqual(raised.exception.code,2)
            execute.assert_not_called()

    @unittest.skipUnless(shutil.which('codex'), 'Codex CLI not installed')
    def test_exact_codex_permissions_pass_real_os_preflight_without_model(self):
        work=self.workspace('codex')
        scratch=self.root/'scratch';scratch.mkdir()
        sentinel=self.root/'private-fixture';sentinel.write_text('synthetic sentinel')
        result=preflight(work,scratch,sentinel)
        self.assertTrue(result.get('safetyPassed'),result)
        self.assertEqual((work/'AGENTS.md').read_text(),'## Rules\nFrozen instructions.\n')
        self.assertEqual((work/'probe-write.txt').read_text(),'ok')
