"""Pinned Codex permissions used for both preflight and actual pilot runs.

The trusted CLI uses its existing login; credentials are never copied or read by
this module. Generated commands may read only their workspace and minimal OS
runtime files. This is a coding-task pilot, not an instruction optimization.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .isolation import capture


def permission_args(workspace, scratch):
    workspace, scratch = str(Path(workspace).resolve()), str(Path(scratch).resolve())
    values = {
        'default_permissions':'atlas-eval',
        'approval_policy':'never',
        'shell_environment_policy.inherit':'none',
        'shell_environment_policy.set.HOME':scratch,
        'shell_environment_policy.set.TMPDIR':scratch,
        'shell_environment_policy.set.PATH':'/Library/Developer/CommandLineTools/usr/bin:/usr/bin:/bin',
        'shell_environment_policy.set.PYTHONDONTWRITEBYTECODE':'1',
        'project_doc_max_bytes':0,
        'web_search':'disabled',
    }
    files = {':root':'deny', ':minimal':'read', '/Library/Developer':'read', workspace:'write',
             workspace + '/AGENTS.md':'read', scratch:'write'}
    table = ','.join(json.dumps(path) + '=' + json.dumps(access) for path,access in files.items())
    # One TOML table avoids CLI dotted-key splitting of quoted filesystem paths.
    result=['-c','permissions={atlas-eval={filesystem={' + table + '},network={enabled=false}}}']
    for key,value in values.items():
        result.extend(['-c',key + '=' + json.dumps(value)])
    return result


def environment(scratch, isolated_home=False):
    env = {'PATH':os.environ.get('PATH','/usr/bin:/bin'),
           'HOME':str(Path.home()),'TMPDIR':str(scratch), 'LANG':'en_US.UTF-8'}
    if isolated_home:
        home = Path(scratch) / 'codex-home'
        home.mkdir(mode=0o700, exist_ok=True)
        env['CODEX_HOME'] = str(home)
    # Preserve transport proxy configuration without copying it into artifacts.
    for key in ('HTTPS_PROXY','HTTP_PROXY','ALL_PROXY','NO_PROXY'):
        if key in os.environ:
            env[key]=os.environ[key]
    return env


def preflight(workspace, scratch, sentinel):
    """No model call: prove the exact selected CLI profile denies host access."""
    binary = shutil.which('codex')
    if not binary:
        return {'status':'infra_error','reason':'codex_unavailable'}
    probe = '''import os,pathlib,socket
blocked=[]
for label, fn in [
 ("host_read",lambda:pathlib.Path(%r).read_bytes()),
 ("host_list",lambda:os.listdir(%r)),
 ("network",lambda:socket.create_connection(("127.0.0.1",9),timeout=1)),
 ("instructions_write",lambda:pathlib.Path("AGENTS.md").write_text("bad"))]:
 try:fn()
 except PermissionError:blocked.append(label)
pathlib.Path("probe-write.txt").write_text("ok")
assert blocked==["host_read","host_list","network","instructions_write"],blocked
print("CODEX_SANDBOX_OK")
''' % (str(sentinel), str(Path(sentinel).parent))
    argv = [binary,'sandbox','-P','atlas-eval','-C',str(workspace)] + permission_args(workspace,scratch)
    argv += [sys.executable,'-I','-B','-c',probe]
    result = capture(argv,workspace,environment(scratch,isolated_home=True),15,65536)
    result['safetyPassed'] = (result['status']=='completed' and result['exitCode']==0
                              and result['stdout'].strip()=='CODEX_SANDBOX_OK')
    return result


def run_codex(workspace, scratch, prompt, model, timeout=180):
    """Explicit caller approval and successful preflight are required by runner."""
    if not isinstance(model,str) or not model or not 1 <= timeout <= 600:
        raise ValueError('explicit model and finite timeout required')
    binary = shutil.which('codex')
    if not binary:
        raise RuntimeError('codex unavailable')
    argv=[binary,'exec','--skip-git-repo-check','--ephemeral','--ignore-user-config',
          '--ignore-rules','--json','--color','never','--model',model,'-C',str(workspace)]
    argv += permission_args(workspace,scratch)
    for feature in ('apps','browser_use','browser_use_external','browser_use_full_cdp_access',
                    'computer_use','hooks','plugins','memories','chronicle','multi_agent',
                    'shell_snapshot','skill_search','view_image','in_app_browser'):
        argv += ['--disable',feature]
    argv += ['--enable','skip_host_skill_discovery',prompt]
    return capture(argv,workspace,environment(scratch),timeout,2*1024*1024)
