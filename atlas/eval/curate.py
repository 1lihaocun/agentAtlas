"""Small, provenance-labelled reconstructions of observed AgentAtlas defects.

These are NOT untouched historical commits and are NOT an optimization dataset.
The current source is frozen, then one documented guard is deliberately removed
per task. Fixed references are separate and never passed to the tested agent.
"""
import hashlib
import json
from pathlib import Path

from atlas.sections import snapshot

RULES = ('## Working rules\nRepair the reported defect with a small change.\n'
         'Preserve public interfaces and existing safety checks.\n'
         'Do not change AGENTS.md. Do not access files outside this workspace.\n')
CASES = (
    ('usage-json', 'usage', 'atlas/usage.py', 'usage-json-v1',
     'child = json.loads(child)', 'child = {"raw_arguments": child}',
     'Structured Codex function-call arguments are JSON-encoded. Quoted paths containing '
     'spaces or Chinese characters are missed, and malformed arguments appear trustworthy. '
     'Fix argument parsing without treating a path mention as a confirmed file read.'),
    ('effective-links', 'effective', 'atlas/effective.py', 'effective-links-v1',
     'if _identity(os.fstat(fd)) != _identity(before) or path.resolve() != target:', 'if False:',
     'An instruction candidate can be replaced between stat and open. The collector must '
     'verify the opened file identity before consuming any bytes; do not weaken the '
     'existing no-follow or final change checks.'),
    ('effective-budget', 'effective', 'atlas/effective.py', 'effective-budget-v1',
     'if before.st_size > min(read_limit, MAX_INSPECTION_BYTES):', 'if False:',
     'A tiny instruction budget still reads an entire large instruction file. Bound actual '
     'inspection work before reading, preserve unknown/partial evidence semantics, and '
     'ensure a zero budget opens no instruction files.'),
)


def build_pilot(destination):
    from .dataset import snapshot_hash, load_dataset
    destination = Path(destination).absolute()
    if destination.exists():
        raise ValueError('refuse to overwrite an existing evaluation bundle')
    destination.mkdir(parents=True, mode=0o700)
    project = Path(__file__).resolve().parents[2]
    source = {name:(project/name).read_bytes() for name in ('atlas/effective.py','atlas/usage.py')}
    instructions = RULES.encode('utf-8')
    parsed = snapshot(instructions)
    rows, references = [], {}
    for case_id, group, target, grader, before, after, task in CASES:
        text = source[target].decode('utf-8')
        if text.count(before) != 1:
            raise ValueError('source drift: reconstruction anchor changed for ' + case_id)
        work = destination / 'snapshots' / case_id
        fixed = destination / 'references' / case_id
        for directory in (work,fixed):
            (directory/'atlas').mkdir(parents=True)
            (directory/'AGENTS.md').write_bytes(instructions)
            for name,content in source.items():
                (directory/name).write_bytes(content)
        (work/target).write_text(text.replace(before,after,1), encoding='utf-8')
        rows.append(dict(id=case_id,group=group,split='train',agent='Codex',task=task,
            snapshot='snapshots/'+case_id,snapshotHash=snapshot_hash(work),cwd='.',
            instructionStack=[dict(relativePath='AGENTS.md',sha256=parsed['version'])],
            target=dict(relativePath='AGENTS.md',sectionId=parsed['sections'][0]['id'],baseVersion=parsed['version']),
            grader=dict(id=grader,timeoutSeconds=10),allowedWritePaths=[target],critical=True,
            provenance=dict(kind='reconstructed',reviewed=True,
                source='Observed AgentAtlas regression; reconstruction, not original Git history. '+target)))
        references[case_id]=dict(path='references/'+case_id,sha256=snapshot_hash(fixed))
    manifest=destination/'tasks.jsonl'
    manifest.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows),encoding='utf-8')
    (destination/'reference-index.json').write_text(json.dumps(references,indent=2),encoding='utf-8')
    (destination/'provenance.json').write_text(json.dumps({
        'kind':'reconstructed','sourceHashes':{k:hashlib.sha256(v).hexdigest() for k,v in source.items()},
        'mutationCount':len(CASES),'optimizationEligible':False,
        'limitations':['not_original_historical_snapshots','small_dependent_sample','no_holdout',
                       'trusted_reference_is_not_an_agent_result']},indent=2),encoding='utf-8')
    load_dataset(manifest,mode='pilot')
    return manifest
