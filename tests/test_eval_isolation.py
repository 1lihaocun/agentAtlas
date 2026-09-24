"""Real, bounded process execution under an OS-enforced filesystem boundary."""
import os
from pathlib import Path
import tempfile
import unittest

from atlas.eval.isolation import run_isolated, run_container


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.home() / '.hermes/cache/scratch')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.grader = self.root / 'grader'
        self.grader.mkdir()
        self.scratch = self.root / 'scratch'
        self.scratch.mkdir()

    def run_code(self, source, timeout=5, max_output=65536):
        script = self.grader / 'probe.py'
        script.write_text(source)
        return run_isolated(script, self.workspace, self.grader, self.scratch,
                            timeout=timeout, max_output=max_output)

    def test_real_script_executes_with_only_approved_scratch_writable(self):
        result = self.run_code('import os, pathlib\npathlib.Path(os.environ["TMPDIR"],"ok").write_text("yes")\nprint("executed")\n')
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['exitCode'], 0, result)
        self.assertEqual(result['stdout'].strip(), 'executed')
        self.assertEqual((self.scratch / 'ok').read_text(), 'yes')

    def test_host_file_and_network_and_grader_write_are_denied(self):
        sentinel = self.root / 'host-sentinel'
        sentinel.write_text('synthetic-private-data')
        source = '''import os, pathlib, socket
blocked=[]
for name, action in [
 ("host_read", lambda:pathlib.Path(%r).read_text()),
 ("host_list", lambda:os.listdir(%r)),
 ("grader_write", lambda:pathlib.Path(__file__).write_text("tampered")),
 ("workspace_write", lambda:pathlib.Path(%r,"bad").write_text("tampered")),
 ("network", lambda:socket.create_connection(("127.0.0.1",9),timeout=1)),
]:
 try:
  action()
 except PermissionError:
  blocked.append(name)
print(",".join(blocked))
''' % (str(sentinel), str(self.root), str(self.workspace))
        result = self.run_code(source)
        self.assertEqual(result['exitCode'], 0, result)
        self.assertEqual(result['stdout'].strip(), 'host_read,host_list,grader_write,workspace_write,network')

    def test_timeout_and_output_limit_are_not_task_failures(self):
        timed = self.run_code('while True: pass\n', timeout=0.25)
        self.assertEqual(timed['status'], 'timeout', timed)
        noisy = self.run_code('while True: print("x"*4096,flush=True)\n', max_output=1024)
        self.assertEqual(noisy['status'], 'infra_error', noisy)
        self.assertEqual(noisy['reason'], 'output_limit')
        self.assertLessEqual(len(noisy['stdout'].encode()) + len(noisy['stderr'].encode()), 1024)

    def test_container_does_not_mount_host_or_allow_network_or_writes(self):
        sentinel=self.root/'private-marker'
        sentinel.write_text('synthetic sentinel')
        script=self.grader/'probe.py'
        script.write_text('''import json,os,pathlib,socket,errno
connection=socket.socket();connection.settimeout(1)
unreachable=connection.connect_ex(("192.0.2.1",9)) in (errno.ENETUNREACH,errno.EHOSTUNREACH)
checks={"host_absent":not pathlib.Path(%r).exists(),
 "no_socket":not pathlib.Path("/var/run/docker.sock").exists(),
 "nonroot":os.geteuid()!=0,
 "no_network":len(pathlib.Path("/proc/net/route").read_text().splitlines())==1 and unreachable,
 "no_credentials":not any(k in os.environ for k in ("OPENAI_API_KEY","ANTHROPIC_API_KEY"))}
for name,path in [("workspace_readonly","/workspace/denied"),("grader_readonly","/grader/denied")]:
 try:pathlib.Path(path).write_text("bad");checks[name]=False
 except OSError:checks[name]=True
pathlib.Path("/tmp/allowed").write_text("ok")
print(json.dumps(checks))
''' % str(sentinel))
        result=run_container(script,self.workspace,self.grader,timeout=15)
        self.assertEqual(result['exitCode'],0,result)
        import json
        checks=json.loads(result['stdout'])
        self.assertTrue(all(checks.values()),checks)


if __name__ == '__main__':
    unittest.main()
