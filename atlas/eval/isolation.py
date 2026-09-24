"""Fail-closed macOS Seatbelt grader sandbox and bounded process capture.

The grader may read only its code, a frozen workspace and OS runtime files. It
cannot read personal HOME, write the workspace/grader, or use the network. This
is an OS boundary, not a cwd convention. Other platforms are unsupported rather
than silently executing untrusted candidate code on the host.
"""
import json
import math
import os
from pathlib import Path
import resource
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import uuid

GRADER_IMAGE = ('public.ecr.aws/docker/library/python@sha256:'
                '2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9')


def run_container(script, workspace, grader, timeout=30, max_output=1024*1024):
    """Pinned minimal image, no HOME/socket/credentials, no network or writable bind."""
    script,workspace,grader=[Path(p).absolute() for p in (script,workspace,grader)]
    for path in (script,workspace,grader):
        if any(p.is_symlink() for p in (path,)+tuple(path.parents)) or any(c in str(path) for c in (',',':','\n')):
            raise ValueError('unsafe container path')
    relative=script.relative_to(grader)
    if workspace==grader or workspace in grader.parents or grader in workspace.parents:
        raise ValueError('grader and workspace must be separate')
    name='agentatlas-grade-'+uuid.uuid4().hex
    argv=['docker','run','--name',name,'--pull=never','--network=none','--read-only',
          '--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=32',
          '--memory=256m','--cpus=1','--user=65534:65534',
          '--ulimit','fsize=8388608:8388608','--ulimit','nofile=128:128',
          '--tmpfs','/tmp:rw,noexec,nosuid,size=32m,mode=1777',
          '--mount','type=bind,src='+str(workspace)+',dst=/workspace,readonly',
          '--mount','type=bind,src='+str(grader)+',dst=/grader,readonly',
          '--workdir','/workspace','--env','HOME=/tmp','--env','TMPDIR=/tmp',
          '--env','PYTHONDONTWRITEBYTECODE=1','--entrypoint','/usr/local/bin/python3',
          GRADER_IMAGE,'-I','-B','/grader/'+relative.as_posix(),'/workspace']
    env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'HOME':str(Path.home())}
    for key in ('DOCKER_HOST','DOCKER_CONTEXT'):
        if key in os.environ:env[key]=os.environ[key]
    try:
        result=capture(argv,workspace,env,timeout,max_output)
        if result['status']=='completed' and result['exitCode'] in (125,126,127):
            result.update(status='infra_error',reason='container_start_failed')
        return result
    finally:
        capture(['docker','rm','--force',name],workspace,env,10,65536)


def capture(argv, cwd, env, timeout, max_output=1024 * 1024):
    """Execute fixed argv, never a shell; bound time, both streams and children."""
    if not argv or isinstance(argv, str) or timeout <= 0 or max_output < 1:
        raise ValueError('invalid process bounds')
    def limits():
        resource.setrlimit(resource.RLIMIT_CPU, (max(1, math.ceil(timeout)), max(2, math.ceil(timeout) + 1)))
        resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    started = time.monotonic()
    out = {'stdout':bytearray(), 'stderr':bytearray()}
    try:
        process = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True, preexec_fn=limits)
    except OSError as error:
        return {'status':'infra_error','reason':type(error).__name__,'exitCode':None,
                'stdout':'','stderr':'','durationMs':0}
    state, reason = 'completed', None
    selector = selectors.DefaultSelector()
    for name in out:
        stream = getattr(process, name)
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, name)
    used = 0
    try:
        while selector.get_map():
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                state, reason = 'timeout', 'deadline'
                break
            for key, _ in selector.select(min(remaining, 0.05)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                available = max_output - used
                out[key.data].extend(chunk[:available])
                used += min(len(chunk), available)
                if len(chunk) > available:
                    state, reason = 'infra_error', 'output_limit'
                    break
            if state != 'completed':
                break
        if state == 'completed':
            try:
                process.wait(timeout=max(0.001, timeout - (time.monotonic() - started)))
            except subprocess.TimeoutExpired:
                state, reason = 'timeout', 'deadline'
    finally:
        # Also kill descendants left behind by a parent that exits successfully.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        selector.close()
        process.stdout.close()
        process.stderr.close()
    return dict(status=state, reason=reason, exitCode=process.returncode,
                durationMs=round((time.monotonic() - started) * 1000),
                **{name:bytes(value).decode('utf-8', 'replace') for name,value in out.items()})


def run_isolated(script, workspace, grader, scratch, timeout=30, max_output=1024 * 1024):
    """Run a trusted grader importing candidate code, with no credentials/network."""
    if sys.platform != 'darwin' or not Path('/usr/bin/sandbox-exec').is_file():
        return {'status':'infra_error','reason':'os_sandbox_unavailable','exitCode':None,
                'stdout':'','stderr':'','durationMs':0}
    script, workspace, grader, scratch = [Path(p).absolute() for p in (script,workspace,grader,scratch)]
    for path in (script,workspace,grader,scratch):
        if any(part.is_symlink() for part in (path,) + tuple(path.parents)):
            raise ValueError('symlink in sandbox path')
    script.relative_to(grader)
    if not script.is_file() or not all(p.is_dir() for p in (workspace,grader,scratch)):
        raise ValueError('sandbox inputs missing')
    if any(a == b or a in b.parents or b in a.parents
           for a,b in ((workspace,grader),(workspace,scratch),(grader,scratch))):
        raise ValueError('sandbox roots must be disjoint')
    quoted = lambda value: json.dumps(str(value))
    policy = '''(version 1)
(deny default)
(allow process*)
(allow sysctl-read)
(allow file-read-metadata)
(allow file-read* (literal "/") (subpath "/System") (subpath "/usr") (subpath "/bin")
 (subpath "/sbin") (subpath "/Library/Developer") (subpath "/Library/Apple")
 (subpath "/private/var/db/dyld") (subpath "/private/var/db/timezone")
 (literal "/private/etc/localtime") (literal "/dev/null")
 (literal "/dev/random") (literal "/dev/urandom"))
(allow file-read* (subpath %s) (subpath %s) (subpath %s))
(allow file-write* (subpath %s) (literal "/dev/null"))
''' % (quoted(workspace),quoted(grader),quoted(scratch),quoted(scratch))
    env = {'PATH':'/usr/bin:/bin','HOME':str(scratch),'TMPDIR':str(scratch),
           'LANG':'en_US.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}
    with tempfile.NamedTemporaryFile(mode='w',suffix='.sb',dir=str(scratch.parent)) as profile:
        profile.write(policy)
        profile.flush()
        return capture(['/usr/bin/sandbox-exec','-f',profile.name,
                        sys.executable,'-I','-B',str(script),str(workspace)],
                       workspace,env,timeout,max_output)
