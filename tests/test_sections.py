"""Pure in-memory fixtures: never read or write instruction files."""
import unittest

from atlas import sections


class SectionTests(unittest.TestCase):
    def test_plain_text_snapshot_has_byte_version_and_editable_whole_chunk(self):
        doc = sections.snapshot(b"abc")
        self.assertEqual(doc["version"],
                         "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        self.assertEqual(len(doc["sections"]), 1)
        chunk = doc["sections"][0]
        self.assertEqual({key: chunk[key] for key in (
            "title", "level", "startByte", "endByte", "text", "hash",
            "start_line", "end_line", "chars", "editable")}, {
                "title": "(全文)", "level": 0, "startByte": 0, "endByte": 3,
                "text": "abc", "hash": doc["version"], "start_line": 1,
                "end_line": 1, "chars": 3, "editable": True,
            })
        self.assertRegex(chunk["id"], r"^[0-9a-f]{64}$")
        self.assertEqual(sections.snapshot(b"abc"), doc)

    def test_flat_atx_chunks_have_utf8_offsets_and_physical_line_ranges(self):
        raw = "序\n# Parent\n甲\n## Same\none\n## Same\ntwo".encode("utf-8")
        doc = sections.snapshot(raw)
        chunks = doc["sections"]
        self.assertEqual([s["title"] for s in chunks],
                         ["(开头)", "Parent", "Same", "Same"])
        self.assertEqual([s["level"] for s in chunks], [0, 1, 2, 2])
        self.assertEqual([(s["startByte"], s["endByte"]) for s in chunks],
                         [(0, 4), (4, 17), (17, 29), (29, 40)])
        self.assertEqual([(s["start_line"], s["end_line"]) for s in chunks],
                         [(1, 1), (2, 3), (4, 5), (6, 7)])
        self.assertEqual([s["text"] for s in chunks],
                         ["序\n", "# Parent\n甲\n", "## Same\none\n", "## Same\ntwo"])
        self.assertEqual([s["chars"] for s in chunks], [2, 11, 12, 11])
        self.assertEqual(len({s["id"] for s in chunks}), 4)
        self.assertEqual(b"".join(raw[s["startByte"]:s["endByte"]] for s in chunks), raw)
        for chunk in chunks:
            self.assertEqual(chunk["hash"], sections.content_version(
                raw[chunk["startByte"]:chunk["endByte"]]))

    def test_roundtrip_preserves_bytes(self):
        samples = [
            b"## A\nfirst\n## B\nsecond\n",
            b"\xef\xbb\xbf## A\r\nfirst\r\n## B\r\nsecond",
            "序\r\n## 标题\n内容\r\n## B\n保留".encode("utf-8"),
            b"plain text without headings", b"", b"\xef\xbb\xbf",
            b"\n\n", b"## A\nfirst\r\n## B\nsecond\r\n",
        ]
        for raw in samples:
            with self.subTest(raw=raw):
                doc = sections.snapshot(raw)
                self.assertEqual(b"".join(raw[s["startByte"]:s["endByte"]]
                                         for s in doc["sections"]), raw)
                for section in doc["sections"]:
                    if section["editable"]:
                        self.assertEqual(sections.replace_section(
                            raw, doc["version"], section["id"], section["text"]), raw)

    def test_replacement_changes_only_selected_chunk_and_uses_local_newlines(self):
        raw = "序\n## A\r\n旧\r\n## B\n保留\n".encode("utf-8")
        doc = sections.snapshot(raw)
        selected = doc["sections"][1]
        for replacement in ("## A\nnew\n", "## A\r\nnew\r\n", "## A\nnew"):
            with self.subTest(replacement=replacement):
                self.assertEqual(sections.replace_section(
                    raw, doc["version"], selected["id"], replacement),
                    "序\n## A\r\nnew\r\n## B\n保留\n".encode("utf-8"))
        last = doc["sections"][-1]
        self.assertEqual(sections.replace_section(raw, doc["version"], last["id"], "## B\nlast"),
                         "序\n## A\r\n旧\r\n## B\nlast".encode("utf-8"))

    def test_bom_is_not_a_heading_character_and_survives_replacement(self):
        raw = b"\xef\xbb\xbf## A\r\nold\r\n## B\r\nkeep"
        doc = sections.snapshot(raw)
        self.assertEqual([s["title"] for s in doc["sections"]], ["A", "B"])
        first = doc["sections"][0]
        self.assertEqual(first["startByte"], 0)
        self.assertEqual(first["text"], "## A\r\nold\r\n")
        self.assertEqual(first["chars"], 11)
        for text in ("## A\nnew", "\ufeff## A\nnew"):
            with self.subTest(text=text):
                self.assertEqual(sections.replace_section(raw, doc["version"], first["id"], text),
                                 b"\xef\xbb\xbf## A\r\nnew\r\n## B\r\nkeep")

    def test_stale_versions_and_unknown_or_stale_ids_are_conflicts(self):
        raw = b"## Same\none\n## Same\ntwo\n"
        old = sections.snapshot(raw)
        changed = raw.replace(b"two", b"new")
        fresh = sections.snapshot(changed)
        # Even the unchanged first chunk is identified within its file version.
        self.assertNotEqual(old["sections"][0]["id"], fresh["sections"][0]["id"])
        cases = [
            (changed, old["version"], old["sections"][0]["id"]),
            (changed, fresh["version"], old["sections"][0]["id"]),
            (raw, old["version"], "0" * 64),
            (raw, "", old["sections"][0]["id"]),
            (raw, None, old["sections"][0]["id"]),
            (raw, old["version"], None),
        ]
        for current, version, section_id in cases:
            with self.subTest(version=version, section_id=section_id):
                with self.assertRaises(sections.Conflict) as caught:
                    sections.replace_section(current, version, section_id, "new")
                self.assertEqual(caught.exception.status, 409)
                self.assertIn("refresh", str(caught.exception).lower())
        second = old["sections"][1]
        self.assertEqual(sections.replace_section(raw, old["version"], second["id"], "## Same\nnew\n"),
                         b"## Same\none\n## Same\nnew\n")

    def test_fences_match_character_length_indentation_and_clean_closers(self):
        bodies = [
            "````\n```\n## Hidden\n````\n",
            "~~~python\n## Hidden\n```\n## Still hidden\n~~~~\n",
            "   ```python\n## Hidden\n   ```  \t\n",
            "```\n    ```\n## Hidden\n``` trailing\n## Still hidden\n```\n",
            "`````\n````\n## Hidden\n``````\n",
            "~~~\n~~~~ junk\n## Hidden\n~~~\n",
        ]
        for body in bodies:
            with self.subTest(body=body):
                raw = ("## A\n" + body + "## B\nend\n").encode("utf-8")
                doc = sections.snapshot(raw)
                self.assertEqual([s["title"] for s in doc["sections"]], ["A", "B"])
                self.assertEqual(doc["sections"][0]["text"], "## A\n" + body)
        for not_fence in ("    ```", "``", "```lang`bad", "\t~~~"):
            with self.subTest(not_fence=not_fence):
                doc = sections.snapshot(("## A\n" + not_fence + "\n## B\n").encode())
                self.assertEqual([s["title"] for s in doc["sections"]], ["A", "B"])
        doc = sections.snapshot(b"## A\n```\n## Hidden through EOF")
        self.assertEqual([s["title"] for s in doc["sections"]], ["A"])

    def test_frontmatter_and_preamble_are_manual_only_and_ignore_markers(self):
        for ending in ("---", "..."):
            with self.subTest(ending=ending):
                raw = ("\ufeff---\r\n# metadata\r\n```\r\n" + ending +
                       "  \r\nintro\r\n## A\r\nbody\r\n## B\r\nend").encode("utf-8")
                doc = sections.snapshot(raw)
                self.assertEqual([s["title"] for s in doc["sections"]], ["(开头)", "A", "B"])
                self.assertEqual([s["optimizable"] for s in doc["sections"]], [False, True, True])
                self.assertTrue(all(s["editable"] for s in doc["sections"]))
                preamble = doc["sections"][0]
                self.assertEqual(sections.replace_section(raw, doc["version"], preamble["id"],
                                                         "manual intro\n"),
                                 b"\xef\xbb\xbfmanual intro\r\n## A\r\nbody\r\n## B\r\nend")
        for raw in (b"---\n# metadata\n---\nintro", b"---\n# unclosed metadata\n## Still metadata"):
            with self.subTest(raw=raw):
                doc = sections.snapshot(raw)
                self.assertEqual(len(doc["sections"]), 1)
                self.assertEqual(doc["sections"][0]["level"], 0)
                self.assertFalse(doc["sections"][0]["optimizable"])
        doc = sections.snapshot(b"intro\n## A\nx\n---\n## B\ny\n")
        self.assertEqual([s["title"] for s in doc["sections"]], ["(开头)", "A", "B"])
        self.assertFalse(doc["sections"][0]["optimizable"])
        for raw in (b"plain", b""):
            self.assertTrue(sections.snapshot(raw)["sections"][0]["optimizable"])

    def test_invalid_utf8_is_rejected_with_422_instead_of_lossy_replacement(self):
        for raw in (b"\xff", b"## A\nok\n## B\n\xc3", b"\xef\xbb\xbf\x80"):
            with self.subTest(raw=raw):
                with self.assertRaises(sections.InvalidUTF8) as caught:
                    sections.snapshot(raw)
                self.assertEqual(caught.exception.status, 422)
                self.assertIn("UTF-8", str(caught.exception))
                with self.assertRaises(sections.InvalidUTF8):
                    sections.replace_section(raw, sections.content_version(raw), "unknown", "safe")
        doc = sections.snapshot(b"abc")
        with self.assertRaises(sections.InvalidUTF8) as caught:
            sections.replace_section(b"abc", doc["version"], doc["sections"][0]["id"], "\ud800")
        self.assertEqual(caught.exception.status, 422)
        for invalid in ("abc", bytearray(b"abc"), None):
            with self.subTest(invalid=invalid), self.assertRaises(TypeError):
                sections.content_version(invalid)
        with self.assertRaises(TypeError):
            sections.snapshot("abc")
        for invalid in (b"abc", None, 123):
            with self.subTest(invalid=invalid), self.assertRaises(TypeError):
                sections.replace_section(b"abc", doc["version"], doc["sections"][0]["id"], invalid)

    def test_atx_titles_keep_inline_hashes_and_accept_only_heading_syntax(self):
        raw = ("# Root #\n## Child ###  \n### C#\n####\n##### \t\n"
               "   ###### Deep ###\n    ## Indented code\n#not-heading\n"
               "####### Too many\n> ## Quoted\n##\u00a0Not ASCII space\n"
               "## ###\n").encode("utf-8")
        chunks = sections.snapshot(raw)["sections"]
        self.assertEqual([s["title"] for s in chunks], ["Root", "Child", "C#", "", "", "Deep", ""])
        self.assertEqual([s["level"] for s in chunks], [1, 2, 3, 4, 5, 6, 2])
        self.assertIn("    ## Indented code\n", chunks[5]["text"])

    def test_parse_sections_compatibility_delegates_to_snapshot(self):
        text = "intro\n## A\nbody\n### B\n```\n# not heading\n```\n"
        parsed = sections.parse_sections(text)
        self.assertEqual(parsed, sections.snapshot(text.encode("utf-8"))["sections"])
        self.assertEqual([s["title"] for s in parsed], ["(开头)", "A", "B"])
        self.assertEqual(parsed[0]["text"], "intro\n")
        self.assertEqual(parsed[0]["start_line"], 1)
        self.assertEqual(parsed[0]["end_line"], 1)
        self.assertEqual(parsed[-1]["end_line"], 7)
        self.assertEqual(sections.parse_sections("")[0]["text"], "")
        with self.assertRaises(TypeError):
            sections.parse_sections(b"bytes are for snapshot")
        with self.assertRaises(sections.InvalidUTF8):
            sections.parse_sections("\udfff")

    def test_last_chunk_without_newline_inherits_nearest_preceding_style(self):
        raw = b"preamble\n## A\r\nbody\r\n## B"
        doc = sections.snapshot(raw)
        selected = doc["sections"][-1]
        self.assertEqual(sections.replace_section(raw, doc["version"], selected["id"], "## B\nnew"),
                         b"preamble\n## A\r\nbody\r\n## B\r\nnew")
        with self.subTest(nearest_separator="LF after CRLF"):
            raw = b"## A\r\nbody\r\n\n## B"
            doc = sections.snapshot(raw)
            self.assertEqual(sections.replace_section(raw, doc["version"],
                                                     doc["sections"][-1]["id"], "## B\nnew"),
                             b"## A\r\nbody\r\n\n## B\nnew")
        plain = sections.snapshot(b"plain")
        self.assertEqual(sections.replace_section(b"plain", plain["version"],
                                                 plain["sections"][0]["id"], "first\nsecond"),
                         b"first\nsecond")

    def test_missing_separator_uses_original_boundary_even_with_mixed_newlines(self):
        raw = b"## A\nold\r\n## B\nkeep"
        doc = sections.snapshot(raw)
        selected = doc["sections"][0]
        self.assertEqual(sections.replace_section(raw, doc["version"], selected["id"], "## A\nnew"),
                         b"## A\nnew\r\n## B\nkeep")
        self.assertEqual(sections.replace_section(raw, doc["version"], selected["id"], ""),
                         b"\r\n## B\nkeep")


if __name__ == "__main__":
    unittest.main()
