"""Isolated trusted-check runner; no automatic model calls or source merging."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

from .isolation import run_container
from .registry import GRADERS, grader_path


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def write_json(path, value):
    path=Path(path)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temporary.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary,path)


def grade(workspace, grader_id, output, timeout=10):
    """Run registered checks under the OS boundary; never import candidate here."""
    output=Path(output)
    output.mkdir(parents=True,exist_ok=False)
    trusted=output/'grader';trusted.mkdir()
    scratch=output/'scratch';scratch.mkdir()
    original=grader_path(grader_id)
    script=trusted/'checks.py'
    shutil.copyfile(original,script)
    result=run_container(script,workspace,trusted,timeout=timeout)
    result.update(score=0,safetyPassed=True,checks={})
    if result['status']=='completed':
        if result['exitCode'] != 0:
            result['reason']='grader_process_failed'
        else:
            try:
                answer=json.loads(result['stdout'])
                checks=answer['checks']
                if (type(answer.get('schemaVersion')) is not int or answer['schemaVersion']!=1
                        or not isinstance(checks,dict) or set(checks)!=set(GRADERS[grader_id][1])
                        or any(type(v) is not bool for v in checks.values())):
                    raise ValueError('invalid grader result')
                result['checks']=checks
                result['score']=int(all(checks.values()))
            except (ValueError,KeyError,TypeError):
                result.update(status='infra_error',reason='invalid_grader_protocol')
    result['graderHash']=hashlib.sha256(original.read_bytes()).hexdigest()
    write_json(output/'grade.json',result)
    return result


def clone_snapshot(source, destination, expected_hash):
    """No-follow copy, then verify the copy before any candidate code runs."""
    from .dataset import snapshot_hash
    source, destination=Path(source),Path(destination)
    if snapshot_hash(source)!=expected_hash:
        raise ValueError('snapshot changed before copy')
    destination.mkdir(parents=True,exist_ok=False)
    total=[0,0]
    def copy_dir(fd,out):
        for name in sorted(os.listdir(fd)):
            info=os.stat(name,dir_fd=fd,follow_symlinks=False)
            import stat
            if stat.S_ISDIR(info.st_mode):
                child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                try:
                    (out/name).mkdir()
                    copy_dir(child,out/name)
                finally:os.close(child)
            elif stat.S_ISREG(info.st_mode):
                child=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
                try:
                    opened=os.fstat(child)
                    if (opened.st_ino,opened.st_dev,opened.st_size)!=(info.st_ino,info.st_dev,info.st_size):
                        raise ValueError('snapshot changed before reading')
                    total[0]+=1;total[1]+=info.st_size
                    if total[0]>200 or total[1]>8*1024*1024:
                        raise ValueError('snapshot copy limit')
                    chunks=[];remaining=info.st_size
                    while remaining:
                        value=os.read(child,min(65536,remaining))
                        if not value:raise ValueError('snapshot shortened')
                        chunks.append(value);remaining-=len(value)
                    if os.fstat(child).st_mtime_ns!=info.st_mtime_ns:
                        raise ValueError('snapshot changed while reading')
                    (out/name).write_bytes(b''.join(chunks))
                finally:os.close(child)
            else:raise ValueError('nonregular snapshot file')
    root=os.open(source,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:copy_dir(root,destination)
    finally:os.close(root)
    if snapshot_hash(destination)!=expected_hash:
        raise ValueError('copied snapshot hash mismatch')


def fingerprint(case):
    return digest({'case':case,'graderHash':hashlib.sha256(grader_path(case['grader']['id']).read_bytes()).hexdigest()})


def result_row(case, result, kind):
    return dict(id=case['id'],status=result['status'],score=result['score'],
                safetyPassed=result['safetyPassed'],critical=case['critical'],
                executionKind=kind,fingerprint=fingerprint(case),checks=result['checks'],
                reason=result.get('reason'),durationMs=result.get('durationMs'),usage=None)


def run_controls(manifest, output):
    """Negative/positive controls are real test executions, NOT agent performance."""
    from .dataset import load_dataset, snapshot_hash
    from .scoring import compare_results
    manifest=Path(manifest).resolve();output=Path(output).absolute()
    cases=load_dataset(manifest,mode='pilot')
    references=json.loads((manifest.parent/'reference-index.json').read_text())
    if set(references)!={case['id'] for case in cases}:
        raise ValueError('reference IDs differ')
    output.mkdir(parents=True,exist_ok=False)
    baseline,candidate=[],[]
    for case in cases:
        ref=references[case['id']]
        reference=manifest.parent/ref['path']
        reference.resolve().relative_to(manifest.parent)
        for arm,source,expected,rows in (
            ('broken',manifest.parent/case['snapshot'],case['snapshotHash'],baseline),
            ('reference',reference,ref['sha256'],candidate)):
            workspace=output/case['id']/arm/'workspace'
            clone_snapshot(source,workspace,expected)
            result=grade(workspace,case['grader']['id'],workspace.parent/'result',case['grader']['timeoutSeconds'])
            rows.append(result_row(case,result,'reconstruction_control'))
            write_json(output/(arm+'-results.json'),rows)
    report=dict(executionKind='reconstruction_control',baseline=baseline,candidate=candidate,
                summary=compare_results(baseline,candidate),optimizationEligible=False,
                limitations=['reconstructed defects','not a Codex result','no holdout','small dependent sample'])
    write_json(output/'report.json',report)
    return report


def audit_changes(original, workspace, allowed):
    from .dataset import snapshot_hash
    snapshot_hash(workspace)  # Reject symlinks, unsafe paths, types and resource excess.
    def inventory(root):
        return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob('*') if p.is_file()}
    old,new=inventory(Path(original)),inventory(Path(workspace))
    changed=sorted(name for name in set(old)|set(new) if old.get(name)!=new.get(name))
    return changed, all(name in allowed for name in changed)


def run_pilot(manifest, output, model, max_calls, timeout=180):
    """A bounded real Codex baseline; never reads the positive-control references."""
    from .dataset import load_dataset, snapshot_hash
    from .codex import preflight, run_codex
    if type(max_calls) is not int or not 1<=max_calls<=10:
        raise ValueError('explicit max_calls between 1 and 10 required')
    manifest=Path(manifest).resolve();output=Path(output).absolute()
    cases=load_dataset(manifest,mode='pilot')
    if len(cases)>max_calls:
        raise ValueError('budget cannot cover all cases; refusing partial score')
    output.mkdir(parents=True,exist_ok=False)
    sentinel=output/'host-sentinel.txt';sentinel.write_text('synthetic isolation marker')
    rows=[]
    for case in cases:
        base=output/case['id'];base.mkdir()
        workspace=base/'workspace';scratch=base/'scratch';scratch.mkdir()
        original=manifest.parent/case['snapshot']
        clone_snapshot(original,workspace,case['snapshotHash'])
        gate=preflight(workspace,scratch,sentinel)
        write_json(base/'preflight.json',gate)
        if not gate.get('safetyPassed'):
            raise RuntimeError('Codex sandbox preflight failed; no model call issued')
        (workspace/'probe-write.txt').unlink()
        # Reserve before launching: interrupted runs never silently reuse budget.
        write_json(base/'reservation.json',dict(case=case['id'],model=model,timeoutSeconds=timeout,
                                               fingerprint=fingerprint(case),maxCalls=max_calls))
        rules=(workspace/'AGENTS.md').read_text()
        prompt=('Perform this isolated coding task. Do not access files outside the workspace. '
                'Only modify these existing files: '+', '.join(case['allowedWritePaths'])+'. '
                'Do not create tests or other files; you may run inline checks. '
                'Use python3 -B to avoid bytecode artifacts.\n\nFrozen instructions:\n'+rules+
                '\nTask:\n'+case['task'])
        execution=run_codex(workspace,scratch,prompt,model,timeout)
        write_json(base/'agent.json',execution)
        safe=False;changed=[]
        try:
            changed,safe=audit_changes(original,workspace,case['allowedWritePaths'])
        except (ValueError,OSError):pass
        if execution['status']!='completed' or execution['exitCode']!=0:
            outcome=dict(status=execution['status'] if execution['status']!='completed' else 'infra_error',
                         score=0,safetyPassed=safe,checks={},reason='agent_execution_failed',
                         durationMs=execution['durationMs'])
        elif not safe:
            outcome=dict(status='completed',score=0,safetyPassed=False,checks={},reason='write_policy_violation')
        else:
            sealed=base/'sealed'
            clone_snapshot(workspace,sealed,snapshot_hash(workspace))
            outcome=grade(sealed,case['grader']['id'],base/'result',case['grader']['timeoutSeconds'])
        row=result_row(case,outcome,'codex_pilot');row['changedFiles']=changed
        rows.append(row)
        write_json(output/'results.json',rows)
    report=dict(executionKind='codex_pilot',model=model,maxCalls=max_calls,timeoutSeconds=timeout,
                status='completed' if all(r['status']=='completed' for r in rows) else 'inconclusive',
                passed=sum(r['score'] for r in rows),total=len(cases),results=rows,
                optimizationEligible=False,costUSD=None,
                limitations=['reconstructed defects','single attempt per task','no holdout',
                             'measures coding repair, not instruction improvement','billing unavailable'])
    write_json(output/'report.json',report)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    curate=commands.add_parser('curate');curate.add_argument('destination')
    validate=commands.add_parser('validate');validate.add_argument('dataset');validate.add_argument('--mode',default='pilot',choices=('test','pilot','baseline','optimize'))
    for name in ('control','pilot'):
        sub=commands.add_parser(name);sub.add_argument('dataset');sub.add_argument('--output',required=True)
        sub.add_argument('--execute',action='store_true')
        if name=='pilot':
            sub.add_argument('--model',required=True);sub.add_argument('--max-calls',required=True,type=int)
            sub.add_argument('--timeout',type=int,default=180)
    report=commands.add_parser('report');report.add_argument('path')
    args=parser.parse_args(argv)
    try:
        if args.command=='curate':
            from .curate import build_pilot
            answer={'dataset':str(build_pilot(args.destination))}
        elif args.command=='validate':
            from .dataset import load_dataset
            rows=load_dataset(args.dataset,mode=args.mode)
            answer={'valid':True,'cases':len(rows),'mode':args.mode}
        elif args.command=='report':
            answer=json.loads(Path(args.path).read_text())
        elif not args.execute:
            parser.error('execution requires --execute; no process started')
        elif args.command=='control':answer=run_controls(args.dataset,args.output)
        else:answer=run_pilot(args.dataset,args.output,args.model,args.max_calls,args.timeout)
        print(json.dumps(answer,ensure_ascii=False,indent=2))
        return 0
    except (OSError,ValueError,RuntimeError) as error:
        print(json.dumps({'ok':False,'error':str(error)},ensure_ascii=False),file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
