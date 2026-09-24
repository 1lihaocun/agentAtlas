"""Deterministic syscall-boundary races using synthetic scratch fixtures only."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atlas import effective


class EffectiveSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.home() / '.hermes/cache/scratch')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.home = self.root / 'codex'
        self.cwd = self.root / 'repo'
        self.home.mkdir()
        self.cwd.mkdir()
        self.rule = self.cwd / 'AGENTS.md'
        self.secret = self.root / 'auth.json'
        self.secret_bytes = b'synthetic-protected-content'
        self.secret.write_bytes(self.secret_bytes)

    def resolve_during_open(self, names, mutate):
        """Observe bytes, not just a post-read rejection; supports old/new I/O."""
        original_path_open, original_os_open, original_read = Path.open, os.open, os.read
        read_chunks = []
        mutated = []

        def before_open(name):
            if Path(os.fsdecode(name)).name in names and not mutated:
                mutated.append(True)
                mutate()

        class ObservedStream:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def read(self, n=-1):
                data = self.stream.read(n)
                read_chunks.append(data)
                return data

        def path_open(path, *args, **kwargs):
            before_open(path)
            return ObservedStream(original_path_open(path, *args, **kwargs))

        def os_open(name, *args, **kwargs):
            before_open(name)
            return original_os_open(name, *args, **kwargs)

        def os_read(fd, count):
            data = original_read(fd, count)
            read_chunks.append(data)
            return data

        with patch.object(Path, 'open', path_open), patch.object(os, 'open', os_open), patch.object(os, 'read', os_read):
            result = effective.resolve_codex(self.cwd, None, self.home)
        self.assertTrue(mutated, 'Race must actually occur')
        self.assertNotIn(self.secret_bytes, b''.join(read_chunks), 'Rejected bytes must never be consumed')
        self.assertEqual(result['sourceStatus'], 'partial')
        self.assertEqual(result['files'], [])

    def test_growth_after_descriptor_check_cannot_extend_read_boundary(self):
        self.rule.write_bytes(b'four')
        original_read = os.read
        consumed = []
        def growing_read(fd, count):
            if not consumed:
                with self.rule.open('ab') as stream:
                    stream.write(b'x' * 262144)
            raw = original_read(fd, count)
            consumed.append(len(raw))
            return raw
        with patch.object(os, 'read', growing_read):
            data = effective.resolve_codex(self.cwd, None, self.home)
        self.assertLessEqual(sum(consumed), 4)
        self.assertEqual(data['sourceStatus'], 'partial')
        self.assertEqual(data['files'], [])

    def test_large_files_have_a_hard_inspection_cap_even_with_large_budget(self):
        with self.rule.open('wb') as stream:
            stream.seek(3 * 1024 * 1024)
            stream.write(b'x')
        with patch.object(os, 'read', side_effect=AssertionError('Oversized files must not be read')):
            data = effective.resolve_codex(self.cwd, None, self.home, max_bytes=4 * 1024 * 1024)
        self.assertEqual(data['sourceStatus'], 'partial')
        self.assertEqual(data['files'], [])

    def test_zero_and_tiny_budgets_do_not_scan_full_instructions(self):
        self.rule.write_bytes(b'x' * 65536)
        for budget in (0, 1):
            with self.subTest(budget=budget), patch.object(os, 'read', side_effect=AssertionError('Unbounded read')):
                data = effective.resolve_codex(self.cwd, None, self.home, max_bytes=budget)
                self.assertEqual(data['sourceStatus'], 'partial')
                self.assertEqual(data['files'], [])

    def test_exhausted_budget_does_not_read_later_directories(self):
        (self.home / 'AGENTS.md').write_bytes(b'full')
        self.rule.write_bytes(b'not included')
        inode = self.rule.stat().st_ino
        original_read = os.read
        def checked_read(fd, count):
            self.assertNotEqual(os.fstat(fd).st_ino, inode, 'Budget already exhausted')
            return original_read(fd, count)
        with patch.object(os, 'read', checked_read):
            data = effective.resolve_codex(self.cwd, None, self.home, max_bytes=4)
        self.assertEqual(len(data['files']), 1)
        self.assertTrue(data['truncated'])

    def test_original_symlink_retargeted_before_open_never_reads_protected_target(self):
        safe = self.cwd / 'safe.md'
        safe.write_bytes(b'safe')
        self.rule.symlink_to(safe)
        def mutate():
            self.rule.unlink()
            self.rule.symlink_to(self.secret)
        self.resolve_during_open({'AGENTS.md', 'safe.md'}, mutate)

    def test_validated_target_replaced_by_symlink_never_reads_protected_target(self):
        self.rule.write_bytes(b'safe')
        def mutate():
            self.rule.unlink()
            self.rule.symlink_to(self.secret)
        self.resolve_during_open({'AGENTS.md'}, mutate)

    def test_regular_file_replacement_is_rejected_before_any_content_read(self):
        self.rule.write_bytes(b'safe')
        def mutate():
            os.replace(self.secret, self.rule)
        self.resolve_during_open({'AGENTS.md'}, mutate)

    def test_unchanged_safe_instruction_symlink_still_works(self):
        safe = self.cwd / 'safe.md'
        safe.write_bytes(b'safe')
        self.rule.symlink_to(safe)
        data = effective.resolve_codex(self.cwd, None, self.home)
        self.assertEqual(data['sourceStatus'], 'available')
        self.assertEqual([row['path'] for row in data['files']], [str(safe)])

    def test_parent_directory_replaced_by_symlink_never_reads_target(self):
        self.rule.write_bytes(b'safe')
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'AGENTS.md').write_bytes(self.secret_bytes)
        def mutate():
            self.cwd.rename(self.root / 'moved')
            self.cwd.symlink_to(outside, target_is_directory=True)
        self.resolve_during_open({'repo', 'AGENTS.md'}, mutate)


if __name__ == '__main__':
    unittest.main()
