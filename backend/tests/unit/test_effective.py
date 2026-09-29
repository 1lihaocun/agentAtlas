
from pathlib import Path
import tempfile
import unittest

from atlas.instructions import effective


class EffectiveTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[3] / ".agentatlas/work"
        scratch.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas-effective-", dir=str(scratch))
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / "codex"
        self.cwd = self.base / "repo" / "child"
        self.home.mkdir()
        self.cwd.mkdir(parents=True)
        self.root = self.cwd.parent

    def put(self, path, raw=b"instructions"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return str(path.resolve())

    def test_override_priority_and_root_to_cwd_order(self):
        global_path = self.put(self.home / "AGENTS.override.md")
        self.put(self.home / "AGENTS.md")
        root_path = self.put(self.root / "AGENTS.md")
        child_path = self.put(self.cwd / "AGENTS.override.md")
        self.put(self.cwd / "AGENTS.md")
        self.put(self.cwd / "CLAUDE.md")
        self.put(self.cwd / "GEMINI.md")
        data = effective.resolve_codex(self.cwd, self.root, self.home)
        self.assertEqual([row["path"] for row in data["files"]],
                         [global_path, root_path, child_path])
        self.assertTrue(all(row["estimated"] for row in data["files"]))
        self.assertIn("assumed_defaults", data["assumptions"])
        self.assertFalse(data["truncated"])

    def test_no_root_checks_only_cwd_empty_override_falls_back(self):
        self.put(self.root / "AGENTS.md")
        self.put(self.cwd / "AGENTS.override.md", b" \n\t")
        expected = self.put(self.cwd / "AGENTS.md", b"cwd")
        self.assertEqual([r["path"] for r in effective.resolve_codex(
            self.cwd, None, self.home)["files"]], [expected])

    def test_byte_cap_reports_partial_and_omitted_files(self):
        first = self.put(self.home / "AGENTS.md", "中文".encode("utf-8"))
        self.put(self.cwd / "AGENTS.md", b"abcde")
        data = effective.resolve_codex(self.cwd, None, self.home, max_bytes=4)
        self.assertTrue(data["truncated"])
        self.assertEqual(len(data["files"]), 1)
        self.assertEqual(data["files"][0]["path"], first)
        self.assertEqual(data["files"][0]["bytes"], 4)
        self.assertEqual(data["files"][0]["totalBytes"], 6)
        self.assertTrue(data["files"][0]["truncated"])
        self.assertEqual(effective.resolve_codex(
            self.cwd, None, self.home, max_bytes=0)["files"], [])

    def test_explicit_fallback_is_last_and_cannot_escape_directory(self):
        expected = self.put(self.cwd / "TEAM.md")
        self.assertEqual(effective.resolve_codex(self.cwd, None, self.home,
                         fallback_names=("TEAM.md",))["files"][0]["path"], expected)
        for name in ("../secret", "/secret", ".", "", "config.toml", ".env"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                effective.resolve_codex(self.cwd, None, self.home, fallback_names=(name,))

    def test_outside_root_rejected_and_symlinks_canonicalized(self):
        with self.assertRaises(ValueError):
            effective.resolve_codex(self.home, self.root, self.home)
        link = self.base / "link"
        link.symlink_to(self.root, target_is_directory=True)
        expected = self.put(self.cwd / "AGENTS.md")
        data = effective.resolve_codex(link / "child", link, self.home)
        self.assertEqual(data["files"][0]["path"], expected)

    def test_nearest_git_marker_and_worktree_not_remapped(self):
        (self.root / ".git").mkdir()
        self.assertEqual(effective.find_project_root(self.cwd), str(self.root))
        (self.cwd / ".git").write_text("gitdir: /unverified/main/.git/worktrees/test\n")
        self.assertEqual(effective.find_project_root(self.cwd), str(self.cwd))
        expected = self.put(self.cwd / "AGENTS.md")
        data = effective.resolve_codex(self.cwd, self.cwd, self.home)
        self.assertEqual(data["files"][0]["path"], expected)
        self.assertEqual(data["mappingStatus"], "unmapped")
        self.assertIn("worktree_mapping_unverified", data["warnings"])

    def test_invalid_budget_rejected(self):
        for budget in (-1, True, 2.5, "100"):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                effective.resolve_codex(self.cwd, None, self.home, max_bytes=budget)

    def test_instruction_symlink_to_secret_is_not_read(self):
        secret = self.base / "auth.json"
        secret.write_text('{"secret":"fixture"}')
        (self.cwd / "AGENTS.md").symlink_to(secret)
        data = effective.resolve_codex(self.cwd, None, self.home)
        self.assertEqual(data["files"], [])
        self.assertEqual(data["sourceStatus"], "partial")

    def test_blank_global_override_and_fallback_scope(self):
        self.put(self.home / "AGENTS.override.md", b"\n")
        expected = self.put(self.home / "AGENTS.md", b"global")
        self.put(self.home / "TEAM.md")
        self.assertEqual([r["path"] for r in effective.resolve_codex(
            self.cwd, None, self.home, fallback_names=("TEAM.md",))["files"]], [expected])
        (self.home / "AGENTS.md").unlink()
        self.assertEqual(effective.resolve_codex(
            self.cwd, None, self.home, fallback_names=("TEAM.md",))["files"], [])

    def test_invalid_utf8_is_unknown_not_silently_replaced(self):
        self.put(self.cwd / "AGENTS.override.md", b"\xff")
        self.put(self.cwd / "AGENTS.md")
        data = effective.resolve_codex(self.cwd, None, self.home)
        self.assertEqual(data["files"], [])
        self.assertEqual(data["sourceStatus"], "partial")

    def test_incomplete_utf8_suffix_is_unknown(self):
        self.put(self.cwd / "AGENTS.md", b"valid text\xc3")
        data = effective.resolve_codex(self.cwd, None, self.home)
        self.assertEqual(data["files"], [])
        self.assertEqual(data["sourceStatus"], "partial")


if __name__ == "__main__":
    unittest.main()
