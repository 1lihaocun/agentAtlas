"""A tiny inspection budget may not read an entire large instruction file."""
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0,sys.argv[1])
from atlas import effective

checks={}
with tempfile.TemporaryDirectory() as temp:
    root=Path(temp)
    work=root/'work';work.mkdir()
    home=root/'codex';home.mkdir()
    (work/'AGENTS.md').write_text('x'*131072)
    sizes=[]
    real_read=os.read
    def watched(fd,size):
        value=real_read(fd,size);sizes.append(len(value));return value
    with patch('os.read',side_effect=watched):
        result=effective.resolve_codex(work,work,home,max_bytes=4)
    checks['bounded_read']=sum(sizes)<=7
    checks['partial_evidence']=result['sourceStatus']=='partial' and result['truncated']
    with patch('os.open',side_effect=AssertionError('zero budget must not open instructions')):
        zero=effective.resolve_codex(work,work,home,max_bytes=0)
    checks['zero_budget_no_open']=not zero['files'] and zero['truncated']
print(json.dumps({'schemaVersion':1,'checks':checks}))
