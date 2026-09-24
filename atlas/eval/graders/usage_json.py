"""Structured arguments must count mentions, not fabricate confirmed reads."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, sys.argv[1])
from atlas.usage import collect_codex

checks = {}
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    logs = root / 'logs'
    logs.mkdir()
    cwd = root / 'cwd'
    cwd.mkdir()
    target = root / 'a space 中文' / 'AGENTS.md'
    target.parent.mkdir()
    target.write_text('rules\n')
    now = 2000000000
    stamp = datetime.fromtimestamp(now - 1, timezone.utc).isoformat()
    def collect(arguments):
        events = [dict(type='session_meta', timestamp=stamp, payload=dict(id='one',cwd=str(cwd))),
                  dict(type='response_item',timestamp=stamp,
                       payload=dict(type='function_call',name='read_file',arguments=arguments))]
        (logs/'sample.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
        return collect_codex(logs,[str(target)],now-100,now,
                             lambda _:dict(files=[],warnings=[],sourceStatus='available'))
    valid = collect(json.dumps({'command':'cat "'+str(target)+'"'}))
    row = valid['files'][0]
    checks['quoted_json_path'] = row['mentions'] == 1 and valid['sourceStatus'] == 'available'
    checks['not_confirmed_read'] = row['confirmedReads'] is None
    invalid = collect('{"path":')
    checks['invalid_arguments_unknown'] = (invalid['sourceStatus'] == 'partial'
                                          and invalid['files'][0]['estimatedSessions'] is None)
print(json.dumps({'schemaVersion':1,'checks':checks}))
