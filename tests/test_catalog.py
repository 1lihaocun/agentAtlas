"""Public-contract tests for the local, credential-safe asset inventory."""
import os
import hashlib
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from atlas import catalog


class CatalogTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(os.environ.get("AGENTATLAS_TEST_TMPDIR", str(Path.home() / ".hermes/cache/scratch")))
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=str(scratch))
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        real_scandir = os.scandir
        def fixture_scandir(path):
            if not isinstance(path, int) and not Path(path).is_relative_to(self.home):
                raise AssertionError('Discovery escaped the isolated fixture')
            return real_scandir(path)
        isolation = mock.patch('os.scandir', side_effect=fixture_scandir)
        isolation.start()
        self.addCleanup(isolation.stop)

    def put(self, path, content="Context\n"):
        target = self.home / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def inventory(self, **kwargs):
        return catalog.discover(home=self.home, **kwargs)

    def by_path(self, data):
        return {item["path"]: item for item in data["files"]}

    def test_topic_names_do_not_override_asset_directory_categories(self):
        fixtures = {
            '.claude/commands/settings.md': 'command',
            '.claude/projects/fixture/memory/config.md': 'memory',
            '.hermes/skills/example/settings.local.md': 'skill',
            '.hermes/skills/example/references/mcp.md': 'reference',
            '.cursor/rules/config.mdc': 'instruction',
        }
        for path in fixtures:
            self.put(path)
        safe_config = self.put('.claude/settings.json', '{"theme":"dark"}')
        self.put('.claude/commands/settings.json', '{"api_key":"synthetic-only"}')
        entries = self.by_path(self.inventory())
        for path, category in fixtures.items():
            with self.subTest(path=path):
                item = entries[str(self.home / path)]
                self.assertEqual(item['category'], category)
                self.assertTrue(item['editable'])
        self.assertEqual(entries[str(safe_config)]['category'], 'config')
        self.assertFalse(entries[str(safe_config)]['editable'])
        self.assertNotIn(str(self.home / '.claude/commands/settings.json'), entries)

    def test_large_config_strings_do_not_trigger_unbounded_regex_backtracking(self):
        code = "from atlas.catalog import is_sensitive_text; assert not is_sensitive_text('x' * 250000); print('checked')"
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                timeout=3, cwd=str(Path(__file__).resolve().parents[1]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "checked")

    def test_platform_tokens_and_sensitive_aliases_are_denied_without_false_env_paths(self):
        for text in ['gateway_token: synthetic-test-only', '{"GITHUB_TOKEN":"synthetic-test-only"}',
                     '{"api\\u005fkey":"synthetic-test-only"}',
                     'headers = { "X-API-Key" = "synthetic-test-only" }']:
            with self.subTest(text=text):
                self.assertTrue(catalog.is_sensitive_text(text))
        self.assertFalse(catalog.is_sensitive_text('{"max_tokens": 1000, "theme":"dark"}'))
        self.assertFalse(catalog.is_sensitive_path("/private/var/example/.claude/CLAUDE.md"))
        self.assertTrue(catalog.is_sensitive_path(self.home / ".gemini/accessToken.json"))

    def test_supplied_instructions_cannot_make_session_or_config_content_editable(self):
        paths = [".hermes/sessions/AGENTS.md", ".hermes/logs/CLAUDE.md",
                 ".hermes/skills/tool/scripts/AGENTS.md"]
        originals = [{"path": str(self.put(path))} for path in paths]
        data = self.inventory(instruction_files=originals)
        self.assertEqual(data["totalFiles"], len(paths))
        self.assertTrue(all(item["category"] == "instruction" for item in data["files"]))
        self.assertTrue(all(not item["editable"] for item in data["files"]))

    def test_missing_inputs_and_unreadable_directories_report_scan_errors(self):
        self.put(".hermes/memories/MEMORY.md")
        blocked = self.home / ".hermes/memories"
        real_scandir = os.scandir

        def scandir(path):
            if str(path) == str(blocked):
                raise PermissionError("Synthetic directory denial")
            return real_scandir(path)

        with mock.patch("os.scandir", side_effect=scandir):
            data = self.inventory(instruction_files=[{"path": str(self.home / "missing/AGENTS.md")}])
        self.assertEqual(data["totalFiles"], 0)
        self.assertEqual(data["scanErrors"], 2)
        self.assertEqual(sum(data["counts"]["categories"].values()), 0)

    def test_large_sessions_and_sqlite_are_metadata_only_and_counted_honestly(self):
        session = self.put(".codex/sessions/2026/09/22/rollout.jsonl", "{}\n")
        with session.open("ab") as stream:
            stream.truncate(8 * 1024 * 1024)
        db = self.put(".hermes/state.db", "SQLite fixture; no database connection needed")
        memory_db = self.put(".openclaw/memory/main.sqlite", "SQLite fixture")
        self.put(".hermes/state.db-wal", "generated")
        with mock.patch("os.open", side_effect=AssertionError("Metadata discovery read file contents")):
            data = self.inventory()
        self.assertEqual(data["totalFiles"], 3)
        items = self.by_path(data)
        self.assertEqual(items[str(session)]["bytes"], 8388608)
        self.assertTrue(items[str(session)]["searchable"])
        self.assertNotIn("sha256", items[str(session)])
        self.assertEqual(items[str(db)]["category"], "session")
        self.assertEqual(items[str(memory_db)]["category"], "memory")
        for path in [db, memory_db]:
            self.assertFalse(items[str(path)]["editable"])
            self.assertFalse(items[str(path)]["searchable"])
            self.assertIn("SQLite", items[str(path)]["reason"])
            with self.assertRaises(PermissionError):
                catalog.read_text(items[str(path)])
        self.assertEqual(data["counts"]["categories"]["session"], 2)
        self.assertEqual(data["counts"]["categories"]["memory"], 1)

    def test_secondary_storage_roots_and_real_session_layouts(self):
        fixtures = {
            ".local/share/opencode/storage/session/project/session.json": ("opencode", "session"),
            ".local/share/opencode/storage/message/session/message.json": ("opencode", "session"),
            ".local/share/opencode/log/server.log": ("opencode", "log"),
            ".local/state/opencode/prompt-history.jsonl": ("opencode", "session"),
            ".gemini/tmp/project/chats/session.json": ("gemini", "session"),
            ".gemini/tmp/project/logs.json": ("gemini", "log"),
            ".cursor/projects/project/agent-transcripts/session.txt": ("cursor", "session"),
            ".config/superpowers/skills/test/SKILL.md": ("shared", "skill"),
            ".claude.json": ("claude", "config"),
        }
        for path in fixtures:
            self.put(path, "{}" if path.endswith(".json") else "Context\n")
        self.put(".gemini/tmp/project/cache/request.json")
        self.put(".gemini/tmp/project/download.bin")
        self.put(".codex/visualizations/run/generated.md")
        self.put(".codex/share/installed/source.py")
        self.put(".gemini/antigravity-backup/brain/task.md")
        data = self.inventory()
        entries = self.by_path(data)
        self.assertEqual(set(entries), {str(self.home / path) for path in fixtures})
        for path, expected in fixtures.items():
            self.assertEqual(tuple(entries[str(self.home / path)][key] for key in ("platform", "category")), expected)

    def test_bounded_reads_return_current_text_and_explicit_prefix_hashes(self):
        path = self.put(".hermes/memories/MEMORY.md", "first\n")
        item = self.inventory()["files"][0]
        path.write_text("updated\n", encoding="utf-8")
        result = catalog.read_text(item)
        self.assertEqual(result["content"], "updated\n")
        self.assertFalse(result["truncated"])
        self.assertEqual(result["bytes"], 8)
        self.assertEqual(result["mtime"], path.stat().st_mtime)
        self.assertEqual(result["sha256"], hashlib.sha256(b"updated\n").hexdigest())
        prefix = catalog.read_text(item, max_bytes=3)
        self.assertEqual(prefix["content"], "upd")
        self.assertTrue(prefix["truncated"])
        self.assertEqual(prefix["bytes"], 8)
        self.assertEqual(prefix["readBytes"], 3)
        self.assertEqual(prefix["sha256Scope"], "prefix")
        self.assertEqual(prefix["sha256"], hashlib.sha256(b"upd").hexdigest())
        self.assertEqual(catalog.read_text(item, max_bytes=0)["content"], "")
        with self.assertRaises(ValueError):
            catalog.read_text(item, max_bytes=-1)

    def test_every_read_revalidates_configs_even_with_a_short_preview(self):
        path = self.put(".claude/settings.json", '{"theme":"dark"}')
        item = self.inventory()["files"][0]
        self.assertEqual(catalog.read_text(item)["content"], '{"theme":"dark"}')
        path.write_text('{"theme":"dark","api_key":"synthetic-test-only"}', encoding="utf-8")
        with self.assertRaises(PermissionError):
            catalog.read_text(item, max_bytes=3)
        path.write_text(" " * 300000, encoding="utf-8")
        with self.assertRaises(PermissionError):
            catalog.read_text(item)

    def test_read_refuses_symlink_swaps_nonregular_files_and_sensitive_paths(self):
        path = self.put(".hermes/memories/MEMORY.md")
        outside = self.put("outside.md")
        item = self.inventory()["files"][0]
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaises(PermissionError):
            catalog.read_text(item)
        path.unlink()
        path.mkdir()
        with self.assertRaises(PermissionError):
            catalog.read_text(item)
        path.rmdir()
        os.mkfifo(str(path))
        with self.assertRaises(PermissionError):
            catalog.read_text(item)
        sensitive = self.put(".hermes/.env", "SYNTHETIC=fixture")
        with self.assertRaises(PermissionError):
            catalog.read_text(dict(item, path=str(sensitive)))
        with self.assertRaises(PermissionError):
            catalog.read_text(dict(item, path=str(outside), searchable=False))

    def test_read_rejects_binary_content_but_decodes_damaged_log_text_safely(self):
        path = self.put(".hermes/logs/gui.log")
        item = self.inventory()["files"][0]
        path.write_bytes(b"SQLite format 3\x00binary")
        with self.assertRaises(ValueError):
            catalog.read_text(item)
        path.write_bytes(b"line: \xff\n")
        result = catalog.read_text(item)
        self.assertEqual(result["content"], "line: \ufffd\n")
        self.assertTrue(result["decodeReplaced"])

    def test_project_identity_is_order_independent_and_keeps_nested_evidence(self):
        parent = self.put('work/repo/AGENTS.md')
        child = self.put('work/repo/a/AGENTS.md')
        nested = self.put('work/repo/vendor-project/src/AGENTS.md')
        (nested.parent.parent / '.git').mkdir()
        explicit = self.put('work/repo/manual/sub/AGENTS.md')
        originals = [{'path': str(path)} for path in (parent, child, nested, explicit)]
        expected = {str(parent): str(parent.parent), str(child): str(parent.parent),
                    str(nested): str(nested.parent.parent),
                    str(explicit): str(explicit.parent.parent)}
        results = []
        for paths in (originals, list(reversed(originals))):
            data = self.inventory(instruction_files=paths,
                                  project_roots=[explicit.parent.parent])
            results.append({item['path']: item['project'] for item in data['files']})
            self.assertEqual(results[-1], expected)
            candidates = {row['path']: row for row in data.get('projectCandidates', [])}
            self.assertEqual(set(candidates), set(expected.values()))
            self.assertIn('git_marker', {row['kind'] for row in candidates[str(nested.parent.parent)]['sources']})
            self.assertIn('explicit', {row['kind'] for row in candidates[str(explicit.parent.parent)]['sources']})
            self.assertTrue(all(row['status'] == 'candidate' for row in candidates.values()))
        self.assertEqual(results[0], results[1])

    def test_project_candidates_and_originals_cannot_bypass_exclusions(self):
        allowed = self.put('work/repo/AGENTS.md')
        excluded = [self.put('work/repo/cache/hidden/AGENTS.md'),
                    self.put('work/repo/node_modules/hidden/AGENTS.md'),
                    self.put('work/repo/credentials/hidden/AGENTS.md')]
        alias = self.home / 'work/linked'
        alias.symlink_to(allowed.parent, target_is_directory=True)
        excluded.append(alias / 'AGENTS.md')
        originals = [{'path': str(path)} for path in [*excluded, allowed]]
        data = self.inventory(instruction_files=originals,
                              project_roots=[path.parent for path in excluded])
        self.assertEqual(set(self.by_path(data)), {str(allowed)})
        self.assertEqual([row['path'] for row in data.get('projectCandidates', [])],
                         [str(allowed.parent)])

    def test_asset_discovery_honors_platform_subroot_without_scanning_siblings(self):
        allowed = self.put('work/repo/.claude/skills/demo/SKILL.md')
        self.put('work/repo/.codex/skills/demo/SKILL.md')
        self.put('work/repo/AGENTS.md')
        root = self.home / 'work/repo/.claude'
        data = self.inventory(scan_roots=[root], max_depth=3)
        self.assertEqual(set(self.by_path(data)), {str(allowed)})
        self.assertEqual([row['path'] for row in data.get('projectCandidates', [])],
                         [str(root.parent)])

    def test_instruction_seed_does_not_disable_discovery_depth_budget(self):
        instruction = self.put('work/repo/AGENTS.md')
        allowed = self.put('work/repo/.claude/skills/demo/SKILL.md')
        self.put('work/repo/.claude/skills/demo/deep/a/b/SKILL.md')
        data = self.inventory(instruction_files=[{'path': str(instruction)}],
                              scan_roots=[self.home / 'work'], max_depth=4)
        self.assertEqual(set(self.by_path(data)), {str(instruction), str(allowed)})

    def test_rejected_instruction_link_cannot_seed_project_asset_discovery(self):
        outside = self.put('outside/note.md')
        self.put('work/linked-seed/.claude/skills/demo/SKILL.md')
        alias = self.home / 'work/linked-seed/AGENTS.md'
        alias.symlink_to(outside)
        data = self.inventory(instruction_files=[{'path': str(alias)}])
        self.assertEqual(data['files'], [])
        self.assertEqual(data.get('projectCandidates'), [])

    def test_discovery_will_not_walk_home_or_override_prune_and_depth(self):
        allowed = self.put('work/repo/.claude/skills/demo/SKILL.md')
        self.put('work/repo/.claude/skills/demo/deep/a/b/SKILL.md')
        self.put('work/Library/hidden/.claude/skills/demo/SKILL.md')
        self.put('work/.Trash-old/hidden/.claude/skills/demo/SKILL.md')
        self.assertEqual(self.inventory(scan_roots=[self.home, self.home.parent])['files'], [])
        alias = self.home / 'linked-work'
        alias.symlink_to(self.home / 'work', target_is_directory=True)
        self.assertEqual(self.inventory(scan_roots=[alias])['files'], [])
        data = self.inventory(scan_roots=[self.home / 'work'], max_depth=4)
        self.assertEqual(set(self.by_path(data)), {str(allowed)})

    def test_git_file_boundary_survives_but_linked_marker_does_not_split_project(self):
        parent = self.put('work/repo/AGENTS.md')
        nested = self.put('work/repo/nested/src/AGENTS.md')
        self.put('work/repo/nested/.git', 'gitdir: fixture-unused')
        linked = self.put('work/repo/linked/src/AGENTS.md')
        (linked.parent.parent / '.git').symlink_to(nested.parent.parent / '.git')
        data = self.inventory(instruction_files=[{'path': str(path)} for path in [linked, nested, parent]])
        entries = self.by_path(data)
        self.assertEqual(entries[str(nested)]['project'], str(nested.parent.parent))
        self.assertEqual(entries[str(linked)]['project'], str(parent.parent))

    def test_unversioned_workspace_is_not_swallowed_by_ancestor_shared_instructions(self):
        shared = self.put('work/collection/CLAUDE.md')
        child = self.put('work/collection/app/selftest/deep/CLAUDE.md')
        asset = self.put('work/collection/app/.hermes/skills/demo/SKILL.md')
        project = self.home / 'work/collection/app'
        for originals in ([shared, child], [child, shared]):
            data = self.inventory(instruction_files=[{'path': str(p)} for p in originals],
                                  scan_roots=[self.home / 'work'], max_depth=8)
            entries = self.by_path(data)
            self.assertEqual(entries[str(child)]['project'], str(project))
            self.assertEqual(entries[str(asset)]['project'], str(project))
            self.assertEqual({r['path'] for r in data['projectCandidates']},
                             {str(shared.parent), str(project)})

    def test_nested_asset_location_in_known_repository_uses_owner_without_disappearing(self):
        instruction = self.put('work/repo/AGENTS.md')
        (instruction.parent / '.git').mkdir()
        asset = self.put('work/repo/area/.claude/skills/demo/SKILL.md')
        data = self.inventory(instruction_files=[{'path': str(instruction)}],
                              scan_roots=[self.home / 'work'], max_depth=6)
        entries = self.by_path(data)
        self.assertEqual(set(entries), {str(instruction), str(asset)})
        self.assertEqual(entries[str(asset)]['project'], str(instruction.parent))
        self.assertEqual([row['path'] for row in data['projectCandidates']], [str(instruction.parent)])

    def test_cached_instruction_cannot_seed_an_otherwise_unknown_project(self):
        cached = self.put('work/repo/.claude/cache/AGENTS.md')
        self.put('work/repo/.claude/skills/demo/SKILL.md')
        data = self.inventory(instruction_files=[{'path': str(cached)}])
        self.assertEqual(data['files'], [])
        self.assertEqual(data.get('projectCandidates'), [])

    def test_explicit_and_inferred_project_roots_do_not_sweep_source_trees(self):
        fixtures = {
            "work/repo/AGENTS.md": ("shared", "instruction"),
            "work/repo/.claude/skills/test/SKILL.md": ("claude", "skill"),
            "work/repo/.claude/commands/review.md": ("claude", "command"),
            "work/repo/.claude/hooks/task.sh": ("claude", "hook"),
            "work/repo/.claude/memory/MEMORY.md": ("claude", "memory"),
            "work/repo/.agents/skills/task/references/guide.md": ("shared", "reference"),
            "work/repo/.cursor/rules/team.mdc": ("cursor", "instruction"),
            "work/repo/.github/instructions/test.instructions.md": ("copilot", "instruction"),
            "work/repo/.opencode/commands/test.md": ("opencode", "command"),
        }
        for path in fixtures:
            self.put(path)
        self.put("work/repo/src/source.py")
        self.put("work/repo/README.md")
        self.put("work/repo/nested/CLAUDE.md")
        self.put("work/unrelated/.claude/skills/task/SKILL.md")
        root = self.home / "work/repo"
        data = self.inventory(project_roots=[root, root / ".", root.parent])
        self.assertEqual(set(self.by_path(data)), {str(self.home / p) for p in fixtures})
        for item in data["files"]:
            self.assertEqual(item["scope"], "project")
            self.assertEqual(item["project"], str(root))
        inferred = self.inventory(instruction_files=[{"path": str(root / "AGENTS.md")}])
        self.assertEqual(set(self.by_path(inferred)), set(self.by_path(data)))

    def test_supplied_same_directory_instruction_alias_is_metadata_only(self):
        target = self.put("work/repo/AGENTS.canonical.md")
        alias = target.parent / "AGENTS.md"
        alias.symlink_to(target.name)
        with mock.patch("os.open", side_effect=AssertionError("Alias contents must not be read")):
            data = self.inventory(instruction_files=[{"path": str(alias), "kind": "agents"}])
        self.assertEqual(data["totalFiles"], 1)
        item = data["files"][0]
        self.assertEqual(item["path"], str(target))
        self.assertEqual(item["category"], "instruction")
        self.assertFalse(item["searchable"])
        self.assertFalse(item["editable"])
        self.assertIn("Symlink", item["reason"])

    def test_instruction_alias_to_config_is_never_opened_and_keeps_platform(self):
        target = self.put("work/repo/settings.json", '{"api_key":"synthetic-test-only"}')
        alias = target.parent / "CLAUDE.md"
        alias.symlink_to(target.name)
        with mock.patch("os.open", side_effect=AssertionError("Restricted alias was opened")):
            data = self.inventory(instruction_files=[{"path": str(alias)}])
        self.assertEqual(data["totalFiles"], 1)
        self.assertFalse(data["files"][0]["searchable"])
        self.assertEqual(data["files"][0]["platform"], "claude")

    def test_symlinks_cannot_escape_roots_or_override_profiles(self):
        allowed = self.put(".hermes/profiles/work/skills/task/AGENTS.md")
        outside = self.put("outside/note.md")
        (self.home / ".hermes/skills").mkdir()
        (self.home / ".hermes/skills/escape.md").symlink_to(outside)
        (self.home / ".hermes/skills/escape-dir").symlink_to(outside.parent, target_is_directory=True)
        (self.home / ".claude").symlink_to(outside.parent, target_is_directory=True)
        (self.home / ".codex").mkdir()
        (self.home / ".codex/cycle").symlink_to(self.home / ".codex", target_is_directory=True)
        self.put("work/repo/.cursor/rules/real.mdc")
        (self.home / "work/alias").symlink_to(self.home / "work/repo", target_is_directory=True)
        data = self.inventory(instruction_files=[{"path": str(allowed)},
                                                {"path": str(self.home / ".hermes/skills/escape.md")}],
                              project_roots=[self.home / "work/alias"])
        self.assertEqual(set(self.by_path(data)), {str(allowed)})
        self.assertFalse(data["files"][0]["editable"])
        self.assertEqual(data["files"][0]["profile"], "work")
        self.assertEqual(data["files"][0]["platform"], "shared")

    def test_editability_never_allows_scripts_configs_sessions_or_other_profiles(self):
        paths = [".hermes/profiles/work/memories/USER.md",
                 ".hermes/profiles/work/skills/task/SKILL.md",
                 ".hermes/skills/task/scripts/README.md",
                 ".claude/hooks/README.md", ".claude/settings.md",
                 ".hermes/sessions/SKILL.md", ".hermes/logs/MEMORY.md"]
        for path in paths:
            self.put(path)
        executable = self.put(".hermes/skills/task/references/executable.md")
        executable.chmod(0o755)
        safe = self.put(".hermes/skills/task/attachments/README.md")
        data = self.inventory()
        entries = self.by_path(data)
        self.assertEqual(len(entries), len(paths) + 2)
        for path in paths + [str(executable)]:
            self.assertFalse(entries[str(self.home / path)]["editable"], path)
        self.assertTrue(entries[str(safe)]["editable"])
        self.assertTrue(catalog.is_sensitive_text('{"apiKey":"synthetic-test-only"}'))
        self.assertTrue(catalog.is_sensitive_text('provider:\n  access_token: synthetic-test-only'))
        self.assertFalse(catalog.is_sensitive_text('{"theme":"dark"}'))

    def test_credentials_and_generated_artifacts_are_excluded_before_reading(self):
        secret_paths = [
            ".hermes/.env", ".hermes/.env.local", ".claude/.credentials.json",
            ".codex/auth.json", ".hermes/mcp-tokens/service.json", ".hermes/secrets/value.txt",
            ".openclaw/credentials/provider.json", ".openclaw/identity/device.json",
            ".claude/keys/private.pem", ".claude/id_ed25519", ".gemini/oauth_creds.json",
            ".hermes/vault.json", ".claude/settings.json.bak", ".hermes/auth.lock",
        ]
        generated = [
            ".hermes/cache/prompts.txt", ".hermes/hermes-agent/source.py",
            ".hermes/node/README.md", ".codex/vendor_imports/pkg/SKILL.md",
            ".codex/tmp/data.json", ".codex/worktrees/tree/repo/source.py",
            ".claude/plugins/cache/pkg/SKILL.md", ".cursor/extensions/pkg/readme.md",
            ".claude/node_modules/pkg/README.md", ".claude/skills/pkg/__pycache__/x.pyc",
            ".claude/backups/config.json", ".gemini/antigravity-browser-profile/Preferences",
            ".hermes/logs/picture.png", ".hermes/sessions/archive.jsonl.gz",
            ".hermes/memories/MEMORY.md.lock",
        ]
        for path in secret_paths + generated:
            self.put(path)
        allowed = self.put(".hermes/memories/USER.md")
        opens = []
        real_open = os.open

        def track_open(path, flags, *args, **kwargs):
            opens.append(str(path))
            return real_open(path, flags, *args, **kwargs)

        with mock.patch("os.open", side_effect=track_open):
            data = self.inventory(instruction_files=[{"path": str(self.home / secret_paths[0])}])
        self.assertEqual(set(self.by_path(data)), {str(allowed)})
        self.assertEqual(opens, [])  # Ordinary metadata and rejected paths are never opened.
        for path in secret_paths[:-3]:
            self.assertTrue(catalog.is_sensitive_path(self.home / path), path)
        self.assertFalse(catalog.is_sensitive_path(allowed))
        self.assertFalse(catalog.is_sensitive_path(self.home / ".codex/memories/tokenization.md"))

    def test_sensitive_config_fields_exclude_whole_file_with_a_size_bound(self):
        configs = {
            ".claude/settings.json": '{"env":{"ANTHROPIC_API_KEY":"synthetic-test-only"}}',
            ".codex/config.toml": 'model="local"\n[provider]\napi_key="synthetic-test-only"\n',
            ".hermes/config.yaml": 'model: local\nproviders:\n  clientSecret: synthetic-test-only\n',
            ".cursor/mcp.json": '{"headers":{"Authorization":"synthetic-test-only"}}',
            ".gemini/settings.json": '{"password":"synthetic-test-only"}',
            ".config/opencode/opencode.jsonc": '{/* comment */ "apiKey":"synthetic-test-only"}',
            ".openclaw/openclaw.json": '{"token":"synthetic-test-only"}',
            ".hermes/skills/task/settings.json": '{"privateKey":"synthetic-test-only"}',
            ".claude/settings.local.json": '{"endpoint":"https://test:test@example.invalid"}',
        }
        for path, content in configs.items():
            self.put(path, content)
        self.put(".gemini/config/huge.json", " " * (2 * 1024 * 1024 + 1))
        safe = self.put(".qwen/settings.json", '{"theme":"dark","model":"local"}')
        data = self.inventory()
        self.assertEqual(set(self.by_path(data)), {str(safe)})
        self.assertFalse(data["files"][0]["editable"])
        self.assertEqual(data["scanErrors"], 0)

    def test_platform_assets_categories_profiles_and_read_only_boundaries(self):
        fixtures = {
            ".hermes/memories/MEMORY.md": ("hermes", "memory", "default", True),
            ".hermes/memories/USER.md": ("hermes", "memory", "default", True),
            ".hermes/profiles/work/memories/USER.md": ("hermes", "memory", "work", False),
            ".hermes/profiles/work/skills/task/SKILL.md": ("hermes", "skill", "work", False),
            ".hermes/skills/task/references/guide.txt": ("hermes", "reference", "default", True),
            ".hermes/skills/task/scripts/task.py": ("hermes", "skill", "default", False),
            ".hermes/sessions/request_dump.json": ("hermes", "session", "default", False),
            ".hermes/logs/gui.log": ("hermes", "log", "default", False),
            ".claude/projects/-work-repo/memory/MEMORY.md": ("claude", "memory", "default", True),
            ".claude/projects/-work-repo/abc.jsonl": ("claude", "session", "default", False),
            ".claude/sessions/active.tmp": ("claude", "session", "default", False),
            ".claude/commands/review.md": ("claude", "command", "default", True),
            ".claude/hooks/task.cjs": ("claude", "hook", "default", False),
            ".claude/settings.json": ("claude", "config", "default", False),
            ".claude/debug/abc.txt": ("claude", "log", "default", False),
            ".codex/memories/raw_memories.md": ("codex", "memory", "default", True),
            ".codex/memories/rollout_summaries/topic.md": ("codex", "memory", "default", True),
            ".codex/archived_sessions/rollout.jsonl": ("codex", "session", "default", False),
            ".codex/rules/default.rules": ("codex", "instruction", "default", False),
            ".gemini/GEMINI.md": ("gemini", "instruction", "default", True),
            ".cursor/rules/style.mdc": ("cursor", "instruction", "default", True),
            ".config/opencode/skill/task/SKILL.md": ("opencode", "skill", "default", True),
            ".codeium/windsurf/memories/topic.md": ("windsurf", "memory", "default", True),
            ".openclaw-autoclaw/workspace/USER.md": ("openclaw", "memory", "autoclaw", True),
            ".openclaw/agents/main/sessions/abc.jsonl": ("openclaw", "session", "default", False),
            ".openclaw/workspace/SOUL.md": ("openclaw", "instruction", "default", True),
            ".qwen/notes.txt": ("qwen", "other", "default", False),
        }
        for path in fixtures:
            self.put(path, '{}' if path.endswith('.json') else 'Context\n')
        data = self.inventory()
        entries = self.by_path(data)
        self.assertEqual(data["totalFiles"], len(fixtures))
        for path, expected in fixtures.items():
            with self.subTest(path=path):
                item = entries[str(self.home / path)]
                self.assertEqual(tuple(item[key] for key in ("platform", "category", "profile", "editable")), expected)
                self.assertTrue(item["searchable"])
        memory = entries[str(self.home / ".claude/projects/-work-repo/memory/MEMORY.md")]
        self.assertEqual(memory["scope"], "project")
        self.assertEqual(memory["project"], "-work-repo")
        self.assertEqual(sum(data["counts"]["categories"].values()), len(fixtures))
        self.assertEqual(sum(data["counts"]["platforms"].values()), len(fixtures))
        self.assertEqual(set(data["counts"]["categories"]), {"instruction", "memory", "skill", "reference", "command", "hook", "config", "session", "log", "other"})

    def test_instruction_input_is_preserved_and_scoped_without_home_walk(self):
        instruction = self.put("work/repo/AGENTS.md")
        claude = self.put(".claude/CLAUDE.md")
        self.put("unrelated/notes.md")
        original = {"path": str(instruction), "kind": "agents", "proj": "repo"}
        data = self.inventory(instruction_files=[original])
        self.assertLess(data["totalFiles"], 20, "Instruction inference escaped the supplied HOME")
        entries = self.by_path(data)
        self.assertEqual(set(entries), {str(instruction), str(claude)})
        item = entries[str(instruction)]
        self.assertEqual(item["platform"], "shared")
        self.assertEqual(item["category"], "instruction")
        self.assertEqual(item["scope"], "project")
        self.assertEqual(item["project"], str(instruction.parent))
        self.assertEqual(item["kind"], "agents")
        self.assertTrue(item["editable"])
        self.assertTrue(item["searchable"])
        self.assertEqual(entries[str(claude)]["scope"], "user")
        self.assertEqual(original, {"path": str(instruction), "kind": "agents", "proj": "repo"})
        self.assertEqual(data["totalFiles"], 2)
        self.assertEqual(data["counts"]["categories"]["instruction"], 2)
        self.assertEqual(data["counts"]["platforms"], {"claude": 1, "shared": 1})
        self.assertEqual(data["scanErrors"], 0)
        self.assertIsInstance(data["generatedAt"], int)
        self.assertIsInstance(item["mtime"], float)
        self.assertEqual(item["mtimeNs"], instruction.stat().st_mtime_ns)
        self.assertEqual(item["bytes"], 8)


if __name__ == "__main__":
    unittest.main()
