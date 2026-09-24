"""A descriptor must be checked before reading a replaced candidate inode."""
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, sys.argv[1])
from atlas import effective

checks = {}
with tempfile.TemporaryDirectory() as temp:
    root=Path(temp)
    work=root/'work';work.mkdir()
    home=root/'codex';home.mkdir()
    target=work/'AGENTS.md';target.write_text('safe\n')
    replacement=root/'replacement';replacement.write_text('synthetic-private-payload\n')
    opened, reads, switched = set(), [], [False]
    real_open, real_read = os.open, os.read
    def race(path,flags,*args,**kwargs):
        if str(path) in ('AGENTS.md',str(target)) and target.exists():
            if not switched[0]:
                os.replace(replacement,target)
                switched[0]=True
            fd=real_open(path,flags,*args,**kwargs)
            opened.add(fd)
            return fd
        return real_open(path,flags,*args,**kwargs)
    def watched(fd,size):
        value=real_read(fd,size)
        if fd in opened: reads.append(len(value))
        return value
    with patch('os.open',side_effect=race),patch('os.read',side_effect=watched):
        result=effective.resolve_codex(work,work,home)
    checks['replaced_target_not_read'] = switched[0] and sum(reads)==0
    checks['replacement_rejected'] = result['sourceStatus']=='partial' and not result['files']
print(json.dumps({'schemaVersion':1,'checks':checks}))
