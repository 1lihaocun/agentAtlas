"""Instruction snapshots share catalog exclusions before opening any body."""

from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from atlas import scan


class ScannerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=str(Path.home()/'.hermes/cache/scratch'))
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()
        self.repo = self.home/'projects'/'repo'
        self.repo.mkdir(parents=True)
        for name, value in [('HOME',str(self.home)), ('ROOTS',[str(self.repo)])]:
            handle=patch.object(scan,name,value);handle.start();self.addCleanup(handle.stop)

    def put(self, relative):
        path=self.repo/relative;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('# Safe synthetic instruction\n',encoding='utf-8')
        return path

    def test_map_excludes_generated_sensitive_and_linked_paths_before_body_reads(self):
        keep=[self.put('AGENTS.md'),self.put('worktrees/owned/AGENTS.md')]
        excluded=[self.put(p) for p in ('tmp/release/AGENTS.md', '.cache/AGENTS.md',
            'credentials/AGENTS.md', '.agentatlas/evaluation/AGENTS.md',
            'plugins/cache/demo/CLAUDE.md')]
        link=self.repo/'sub'/'CLAUDE.md';link.parent.mkdir();link.symlink_to(keep[0])
        folder=self.repo/'linked';folder.symlink_to(keep[0].parent, target_is_directory=True)
        real_meta=scan.file_meta;reads=[]
        def checked_meta(path):
            reads.append(path)
            self.assertNotIn(path,{str(p) for p in excluded+[link]})
            return real_meta(path)
        with patch.object(scan,'file_meta',side_effect=checked_meta):
            result=scan.scan()
        self.assertEqual({f['path'] for f in result['files']},{str(p) for p in keep})
        self.assertEqual(set(reads),{str(p) for p in keep})

    def test_snapshot_is_atomically_replaced_after_full_serialization(self):
        self.put('AGENTS.md')
        destination=self.repo/'atlas.json'
        old=b'{"old":"complete snapshot"}'
        destination.write_bytes(old)
        replace=os.replace
        observed=[]
        def inspect_replace(source,target):
            self.assertEqual(Path(target),destination)
            self.assertEqual(destination.read_bytes(),old)
            self.assertEqual(json.loads(Path(source).read_bytes())['totalFiles'],1)
            observed.append(True)
            return replace(source,target)
        with patch.object(scan,'__file__',str(self.repo/'atlas'/'scan.py')), \
                patch('os.replace',side_effect=inspect_replace), patch('builtins.print'):
            scan.main()
        self.assertEqual(observed,[True],'published snapshots must use atomic replacement')
        self.assertEqual(json.loads(destination.read_bytes())['totalFiles'],1)

    def test_source_replaced_by_symlink_after_walk_is_not_read(self):
        source=self.put('AGENTS.md')
        outside=self.home/'outside.md';outside.write_text('synthetic alternate content')
        real_meta=scan.file_meta
        def swap(path):
            source.unlink();source.symlink_to(outside)
            return real_meta(path)
        with patch.object(scan,'file_meta',side_effect=swap):
            result=scan.scan()
        self.assertEqual(result['files'],[])
        self.assertEqual(result['scanErrors'],1)


if __name__=='__main__':
    unittest.main()
