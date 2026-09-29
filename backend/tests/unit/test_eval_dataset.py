"""Evaluation dataset tests; all fixture files live in runtime scratch."""
import copy
import hashlib
import json

from atlas.files import sections
import os
from pathlib import Path
import tempfile
import unittest

from atlas.evaluation.dataset import load_dataset, snapshot_hash


class DatasetTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[3] / ".agentatlas/work"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="atlas-dataset-", dir=str(scratch))
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "snapshot"
        self.root.mkdir()

    def record(self):
        raw = b"# Guidance\nUse structured JSON output.\n"
        (self.root / "AGENTS.md").write_bytes(raw)
        (self.root / "solution.py").write_bytes(b"def main():\n    return {}\n")
        doc = sections.snapshot(raw)
        return {
            "id": "case-1", "group": "usage", "split": "train", "agent": "Codex",
            "task": "Return a usage report as JSON.", "snapshot": "snapshot",
            "snapshotHash": snapshot_hash(self.root), "cwd": ".",
            "instructionStack": [{"relativePath": "AGENTS.md", "sha256": doc["version"]}],
            "target": {"relativePath": "AGENTS.md", "sectionId": doc["sections"][0]["id"],
                       "baseVersion": doc["version"]},
            "grader": {"id": "usage-json-v1", "timeoutSeconds": 30},
            "allowedWritePaths": ["solution.py"], "critical": True,
            "provenance": {"kind": "fixture", "reviewed": False,
                           "source": "Unit-test fixture, not observed agent data."},
        }

    def manifest(self, records):
        path = self.base / "manifest.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
        return path

    def test_load_fixture_in_test_mode_returns_original_records(self):
        from atlas.evaluation.dataset import load_dataset
        row = self.record()
        path = self.manifest([row])
        self.assertEqual(load_dataset(path, mode="test"), [row])
        self.assertFalse((self.root / "__init__.py").exists())

    def test_schema_requires_exact_keys_types_and_values(self):
        row = self.record()
        mutations = []
        for key in row:
            candidate = copy.deepcopy(row)
            del candidate[key]
            mutations.append(("missing " + key, candidate))
        invalid = {
            "id": [None, True, 42, "", "  ", "../escape", "/absolute", "a/b", "a\\b", "a\n", ".", "a"*129], "group": [None, 1, ""],
            "split": ["dev", "Train", False, []], "agent": ["Claude", "codex", None],
            "task": [None, {}, ""], "snapshot": [None, 1], "cwd": [None, 1],
            "snapshotHash": ["A" * 64, "a" * 63, "a" * 65, "a" * 64 + "\n", None],
            "instructionStack": [None, {}, "AGENTS.md", [None]],
            "target": [None, [], "AGENTS.md"], "grader": [None, [], "usage-json-v1"],
            "allowedWritePaths": [None, "solution.py", [None]],
            "critical": [0, 1, "true", None], "provenance": [None, [], "real"],
        }
        for key, values in invalid.items():
            for value in values:
                candidate = copy.deepcopy(row)
                candidate[key] = value
                mutations.append((key + "=" + repr(value), candidate))
        for name, candidate in mutations:
            with self.subTest(name=name), self.assertRaises(ValueError):
                load_dataset(self.manifest([candidate]), mode="test")

    def test_nested_schemas_reject_unknown_missing_and_wrong_types(self):
        row = self.record()
        nested = [
            ("grader", {"id": ["shell", "usage-json-v2", None, []],
                         "timeoutSeconds": [True, False, 0, 121, 1.5, "30", None]}),
            ("target", {"relativePath": [None], "sectionId": ["x", "A" * 64, None],
                        "baseVersion": ["x", "A" * 64, None]}),
            ("provenance", {"kind": ["observed", None, []], "reviewed": [1, 0, None, "yes"],
                            "source": [None, "", {}, " "]}),
        ]
        for key, invalid in nested:
            for field in row[key]:
                candidate = copy.deepcopy(row)
                del candidate[key][field]
                with self.subTest(missing=key + "." + field), self.assertRaises(ValueError):
                    load_dataset(self.manifest([candidate]), mode="test")
            for field, values in invalid.items():
                for value in values:
                    candidate = copy.deepcopy(row)
                    candidate[key][field] = value
                    with self.subTest(key=key, field=field, value=value), self.assertRaises(ValueError):
                        load_dataset(self.manifest([candidate]), mode="test")
        for key in [None, "grader", "target", "provenance", "instructionStack"]:
            candidate = copy.deepcopy(row)
            obj = candidate if key is None else candidate[key]
            if key == "instructionStack":
                obj = obj[0]
            obj["command"] = "arbitrary-command"
            with self.subTest(extra=key), self.assertRaises(ValueError):
                load_dataset(self.manifest([candidate]), mode="test")
        for value in [{}, {"relativePath": "AGENTS.md"},
                      {"relativePath": "AGENTS.md", "sha256": "A" * 64}]:
            candidate = copy.deepcopy(row)
            candidate["instructionStack"] = [value]
            with self.subTest(instruction=value), self.assertRaises(ValueError):
                load_dataset(self.manifest([candidate]), mode="test")

    def test_grader_allowlist_and_timeout_boundaries(self):
        row = self.record()
        for grader in ["usage-json-v1", "effective-links-v1", "effective-budget-v1"]:
            for timeout in [1, 120]:
                row["grader"] = {"id": grader, "timeoutSeconds": timeout}
                with self.subTest(grader=grader, timeout=timeout):
                    self.assertEqual(load_dataset(self.manifest([row]), mode="test"), [row])

    def test_mode_provenance_gates_and_default_pilot(self):
        row = self.record()
        # Synthetic schema inputs only: this never promotes fixtures to a real dataset.
        for kind in ["fixture", "reconstructed", "real"]:
            for reviewed in [False, True]:
                row["provenance"].update(kind=kind, reviewed=reviewed)
                path = self.manifest([row])
                for mode in ["test", "pilot", "baseline"]:
                    valid = (mode == "test" or reviewed and
                             (mode == "pilot" and kind in ["reconstructed", "real"] or
                              mode == "baseline" and kind == "real"))
                    with self.subTest(kind=kind, reviewed=reviewed, mode=mode):
                        if valid:
                            self.assertEqual(load_dataset(path, mode=mode), [row])
                        else:
                            with self.assertRaises(ValueError):
                                load_dataset(path, mode=mode)
                if kind == "fixture":
                    with self.assertRaises(ValueError):
                        load_dataset(path)
        for mode in ["production", "", None, True, []]:
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                load_dataset(path, mode=mode)

    def optimization_records(self):
        row = self.record()
        records = []
        for index in range(20):
            candidate = copy.deepcopy(row)
            candidate.update(id="case-" + str(index), group="group-" + str(index % 3),
                             split=["train", "validation", "test"][index % 3])
            candidate["provenance"].update(kind="real", reviewed=True)
            records.append(candidate)
        return records

    def test_optimize_requires_twenty_reviewed_real_three_groups_all_splits(self):
        records = self.optimization_records()
        self.assertEqual(load_dataset(self.manifest(records), mode="optimize"), records)
        candidates = [records[:19]]
        for mutation in ["review", "kind", "splits", "groups"]:
            rows = copy.deepcopy(records)
            if mutation == "review":
                rows[0]["provenance"]["reviewed"] = False
            elif mutation == "kind":
                rows[0]["provenance"]["kind"] = "reconstructed"
            elif mutation == "splits":
                for row in rows:
                    row["split"] = "train"
            else:
                for row in rows:
                    row.update(group="only", split="train")
            candidates.append(rows)
        for rows in candidates:
            with self.subTest(rows=rows[0]), self.assertRaises(ValueError):
                load_dataset(self.manifest(rows), mode="optimize")

    def test_unique_ids_group_isolation_and_duplicate_path_lists(self):
        row = self.record()
        with self.assertRaises(ValueError):
            load_dataset(self.manifest([row, row]), mode="test")
        other = copy.deepcopy(row)
        other.update(id="case-2", split="test")
        with self.assertRaises(ValueError):
            load_dataset(self.manifest([row, other]), mode="test")
        other["split"] = "train"
        self.assertEqual(load_dataset(self.manifest([row, other]), mode="test"), [row, other])
        for field in ["allowedWritePaths", "instructionStack"]:
            candidate = copy.deepcopy(row)
            candidate[field] *= 2
            with self.subTest(field=field), self.assertRaises(ValueError):
                load_dataset(self.manifest([candidate]), mode="test")

    def test_manifest_empty_bad_json_duplicate_keys_and_size_limits(self):
        row = self.record()
        path = self.manifest([])
        for text in ["", " \n", "[]\n", "null\n", "{broken}\n", "{}\n",
                     json.dumps(row) + "\n\n", '{"id": "x", "id": "y"}\n',
                     json.dumps(row).replace('"critical": true', '"critical": NaN')]:
            path.write_text(text, encoding="utf-8")
            with self.subTest(text=text[:70]), self.assertRaises(ValueError):
                load_dataset(path, mode="test")
        path.write_bytes(b"\xff")
        with self.assertRaises(ValueError):
            load_dataset(path, mode="test")
        path.write_bytes(b" " * (1024 * 1024 + 1))
        with self.assertRaises(ValueError):
            load_dataset(path, mode="test")
        rows = []
        for index in range(1001):
            candidate = copy.deepcopy(row)
            candidate["id"] = "c" + str(index)
            rows.append(candidate)
        path = self.manifest(rows)
        self.assertLess(path.stat().st_size, 1024 * 1024)
        with self.assertRaises(ValueError):
            load_dataset(path, mode="test")

    def test_relative_paths_are_literal_canonical_and_confined(self):
        row = self.record()
        invalid = ["", "/etc/passwd", "../outside.py", "sub/../solution.py", "a\\b.py",
                   "C:/escape.py", "./solution.py", "a//b.py", "solution.py/", "*", "a\x00.py"]
        for value in invalid:
            for field in ["snapshot", "cwd", "instruction", "target", "allowed"]:
                candidate = copy.deepcopy(row)
                if field in ["snapshot", "cwd"]:
                    candidate[field] = value
                elif field == "instruction":
                    candidate["instructionStack"][0]["relativePath"] = value
                elif field == "target":
                    candidate["target"]["relativePath"] = value
                else:
                    candidate["allowedWritePaths"] = [value]
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    load_dataset(self.manifest([candidate]), mode="test")

    def test_hashes_must_match_actual_snapshot_instruction_and_target(self):
        row = self.record()
        for field in ["snapshotHash", "instruction", "baseVersion", "sectionId"]:
            candidate = copy.deepcopy(row)
            if field == "snapshotHash":
                candidate[field] = "0" * 64
            elif field == "instruction":
                candidate["instructionStack"][0]["sha256"] = "0" * 64
            else:
                candidate["target"][field] = "0" * 64
            with self.subTest(field=field), self.assertRaises(ValueError):
                load_dataset(self.manifest([candidate]), mode="test")
        (self.root / "solution.py").write_bytes(b"changed after hash")
        with self.assertRaises(ValueError):
            load_dataset(self.manifest([row]), mode="test")

    def test_only_optimizable_sections_can_be_targets(self):
        row = self.record()
        for raw in [b"Preamble\n# Heading\nBody\n", b"---\nkind: fixture\n---\n# Heading\nBody\n"]:
            (self.root / "AGENTS.md").write_bytes(raw)
            doc = sections.snapshot(raw)
            row["snapshotHash"] = snapshot_hash(self.root)
            row["instructionStack"][0]["sha256"] = doc["version"]
            row["target"].update(baseVersion=doc["version"], sectionId=doc["sections"][0]["id"])
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                load_dataset(self.manifest([row]), mode="test")
        raw = b"\xff"
        (self.root / "AGENTS.md").write_bytes(raw)
        row["snapshotHash"] = snapshot_hash(self.root)
        row["instructionStack"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
        row["target"]["baseVersion"] = hashlib.sha256(raw).hexdigest()
        with self.assertRaises(ValueError):
            load_dataset(self.manifest([row]), mode="test")

    def test_cwd_and_references_must_exist_with_expected_types(self):
        row = self.record()
        (self.root / "nested").mkdir()
        row["cwd"] = "nested"
        self.assertEqual(load_dataset(self.manifest([row]), mode="test"), [row])
        for cwd in ["missing", "solution.py"]:
            row["cwd"] = cwd
            with self.subTest(cwd=cwd), self.assertRaises(ValueError):
                load_dataset(self.manifest([row]), mode="test")
        row["cwd"] = "."
        for field in ["allowed", "instruction", "target"]:
            for value in ["missing.py", "nested"]:
                candidate = copy.deepcopy(row)
                if field == "allowed":
                    candidate["allowedWritePaths"] = [value]
                elif field == "instruction":
                    candidate["instructionStack"][0]["relativePath"] = value
                else:
                    candidate["target"]["relativePath"] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    load_dataset(self.manifest([candidate]), mode="test")
        row["allowedWritePaths"] = ["AGENTS.md"]
        with self.assertRaises(ValueError):
            load_dataset(self.manifest([row]), mode="test")
        row["allowedWritePaths"] = ["solution.py"]
        row["instructionStack"] = []
        with self.assertRaises(ValueError):
            load_dataset(self.manifest([row]), mode="test")

    def test_snapshot_root_and_all_symlink_ancestors_are_rejected(self):
        row = self.record()
        path = self.manifest([row])
        alias = self.base / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            snapshot_hash(alias)
        row["snapshot"] = "alias"
        with self.assertRaises(ValueError):
            load_dataset(self.manifest([row]), mode="test")
        outer = self.base / "outer"
        outer.symlink_to(self.base, target_is_directory=True)
        for root in [outer / "snapshot", outer / "snapshot" / ".." / "snapshot"]:
            with self.subTest(root=root), self.assertRaises(ValueError):
                snapshot_hash(root)
        with self.assertRaises(ValueError):
            load_dataset(outer / path.name, mode="test")
        link = self.base / "linked.jsonl"
        link.symlink_to(path)
        with self.assertRaises(ValueError):
            load_dataset(link, mode="test")

    def test_snapshot_internal_symlinks_and_special_files_are_rejected(self):
        row = self.record()
        for target in [self.root / "AGENTS.md", self.base / "absent", self.base]:
            alias = self.root / "alias.md"
            alias.symlink_to(target)
            try:
                with self.subTest(target=target), self.assertRaises(ValueError):
                    snapshot_hash(self.root)
                with self.assertRaises(ValueError):
                    load_dataset(self.manifest([row]), mode="test")
            finally:
                alias.unlink()
        fifo = self.root / "fifo.txt"
        os.mkfifo(str(fifo))
        try:
            with self.assertRaises(ValueError):
                snapshot_hash(self.root)
        finally:
            fifo.unlink()
        os.link(str(self.root / "AGENTS.md"), str(self.root / "alias.md"))
        with self.assertRaises(ValueError):
            snapshot_hash(self.root)

    def test_sensitive_names_disallowed_extensions_and_generated_dirs(self):
        self.record()
        names = ["auth.json", "AUTH.md", "credentials.json", "credential.txt", ".env",
                 ".env.example", "config.json", "config.md", "secrets.txt", "token.json",
                 "id_rsa.txt", "script.sh", "package.bin", "data.jsonl"]
        for name in names:
            path = self.root / name
            path.write_bytes(b"fixture")
            try:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    snapshot_hash(self.root)
            finally:
                path.unlink()
        for name in [".git", "node_modules", "__pycache__", ".ssh", ".aws", ".config"]:
            path = self.root / name
            path.mkdir()
            try:
                with self.subTest(directory=name), self.assertRaises(ValueError):
                    snapshot_hash(self.root)
            finally:
                path.rmdir()

    def test_snapshot_rejects_credential_material_even_in_ordinary_files(self):
        self.record()
        path = self.root / "notes.txt"
        for raw in [b"-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n",
                    b'{"api_key": "synthetic-nonsecret-test-value"}',
                    b'ACCESS_TOKEN="synthetic-nonsecret-test-value"\n']:
            path.write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                snapshot_hash(self.root)

    def test_snapshot_limits_empty_missing_and_regular_file_roots(self):
        with self.assertRaises(ValueError):
            snapshot_hash(self.root)
        with self.assertRaises(ValueError):
            snapshot_hash(self.root / "missing")
        for index in range(200):
            (self.root / (str(index) + ".txt")).write_bytes(b"")
        self.assertRegex(snapshot_hash(self.root), r"^[0-9a-f]{64}$")
        excess = self.root / "excess.txt"
        excess.write_bytes(b"")
        with self.assertRaises(ValueError):
            snapshot_hash(self.root)
        excess.unlink()
        large = self.root / "0.txt"
        large.write_bytes(b"x" * (8 * 1024 * 1024))
        self.assertRegex(snapshot_hash(self.root), r"^[0-9a-f]{64}$")
        (self.root / "1.txt").write_bytes(b"x")
        with self.assertRaises(ValueError):
            snapshot_hash(self.root)
        with self.assertRaises(ValueError):
            snapshot_hash(large)
        with self.assertRaises(ValueError):
            load_dataset(self.base / "missing.jsonl", mode="test")
        with self.assertRaises(ValueError):
            load_dataset(self.root, mode="test")

    def test_duplicate_keys_cannot_override_otherwise_valid_records(self):
        row = self.record()
        text = json.dumps(row)
        path = self.base / "manifest.jsonl"
        for candidate in [text[:-1] + ', "critical": false}',
                          text.replace('"timeoutSeconds": 30',
                                       '"timeoutSeconds": 121, "timeoutSeconds": 30')]:
            path.write_text(candidate, encoding="utf-8")
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                load_dataset(path, mode="test")

    def test_snapshot_source_is_never_executed(self):
        row = self.record()
        (self.root / "__init__.py").write_text("raise RuntimeError('must never import snapshot')\n")
        row["snapshotHash"] = snapshot_hash(self.root)
        self.assertEqual(load_dataset(self.manifest([row]), mode="test"), [row])

    def test_jsonl_preserves_unicode_line_separators_inside_strings(self):
        row = self.record()
        row["task"] = "Report usage\u2028without dropping Unicode."
        path = self.base / "manifest.jsonl"
        path.write_text(json.dumps(row, ensure_ascii=False) + "\r\n", encoding="utf-8")
        self.assertEqual(load_dataset(path, mode="test"), [row])
        path.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(load_dataset(path, mode="test"), [row])

    def test_manifest_exact_byte_and_row_limits_are_inclusive(self):
        row = self.record()
        text = json.dumps(row)
        path = self.base / "manifest.jsonl"
        path.write_bytes(text.encode("utf-8") + b" " * (1024 * 1024 - len(text)))
        self.assertEqual(load_dataset(path, mode="test"), [row])
        records = [dict(row, id="c" + str(index)) for index in range(1000)]
        self.assertEqual(load_dataset(self.manifest(records), mode="test"), records)

    def test_more_credential_spellings_are_rejected(self):
        self.record()
        path = self.root / "notes.md"
        samples = [b"OPENAI_API_KEY=synthetic-nonsecret-test-value\n",
                   b"export ACCESS_TOKEN=synthetic-nonsecret-test-value\n",
                   b'{"token": "synthetic-nonsecret-test-value"}',
                   b'{"apiKey": "synthetic-nonsecret-test-value"}']
        for raw in samples:
            path.write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                snapshot_hash(self.root)

    def test_snapshot_hash_is_sorted_length_framed_paths_and_bytes(self):
        files = {"z.txt": b"last\n", "nested/a.md": "# First\n甲\n".encode("utf-8")}
        for name, raw in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        digest = hashlib.sha256()
        for name in sorted(files):
            encoded = name.encode("utf-8")
            raw = files[name]
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
            digest.update(len(raw).to_bytes(8, "big"))
            digest.update(raw)
        self.assertEqual(snapshot_hash(self.root), digest.hexdigest())
        self.assertEqual(snapshot_hash(str(self.root)), digest.hexdigest())
        (self.root / "z.txt").write_bytes(b"changed")
        self.assertNotEqual(snapshot_hash(self.root), digest.hexdigest())


if __name__ == "__main__":
    unittest.main()
