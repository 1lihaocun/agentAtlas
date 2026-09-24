"""Pure, byte-preserving flat ATX chunks (Python 3.9 stdlib).

This is a section locator, not a full Markdown renderer. IDs hash the ASCII
string ``fileVersion:startByte:endByte`` and are scoped to the exact bytes,
not a path; callers must authorize the file separately before applying edits.
"""
import hashlib
import re


_HEAD_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|$)")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


class Conflict(ValueError):
    """The caller's version or section no longer matches the supplied bytes."""

    status = 409
    code = "section_conflict"


class InvalidUTF8(ValueError):
    """Invalid source bytes or replacement text must never be saved lossily."""

    status = 422
    code = "invalid_utf8"


def content_version(raw: bytes) -> str:
    """Hash original bytes, independently of text encoding or newline style."""
    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    return hashlib.sha256(raw).hexdigest()


def snapshot(raw: bytes) -> dict:
    """Return a version and flat chunks covering every original byte.

    Byte ranges are half-open; line numbers are one-based and inclusive,
    without a phantom line after a final newline (empty input occupies line 1).
    A leading UTF-8 BOM belongs to the first byte range/hash, but is omitted
    from its editor text/chars. All chunks retain their original line endings.

    ``editable`` permits manual edits. ``optimizable`` excludes preambles and
    frontmatter, including an unclosed initial YAML block. Optimizers must
    check that flag AND their own user-approved allowlist; it is not a complete
    safety classification. Heading-free plain text remains a whole-file chunk.
    """
    version = content_version(raw)
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise InvalidUTF8("Source is not valid UTF-8; refusing lossy section editing.") from error
    lines = raw.splitlines(keepends=True)
    marks = []
    offset = 0
    fence = None
    frontmatter = False
    has_frontmatter = False
    for index, line in enumerate(lines):
        body = line.decode("utf-8-sig" if index == 0 else "utf-8").rstrip("\r\n")
        start = offset
        offset += len(line)
        if index == 0 and body.rstrip(" \t") == "---":
            frontmatter = has_frontmatter = True
            continue
        if frontmatter:
            if body.rstrip(" \t") in ("---", "..."):
                frontmatter = False
            continue
        candidate = _FENCE_RE.match(body)
        if fence is not None:
            if candidate:
                run, suffix = candidate.groups()
                if run[0] == fence[0] and len(run) >= fence[1] and not suffix.strip(" \t"):
                    fence = None
            continue
        if candidate:
            run, suffix = candidate.groups()
            if run[0] == "~" or "`" not in suffix:
                fence = (run[0], len(run))
                continue
        heading = _HEAD_RE.match(body)
        if heading:
            title = re.sub(r"(?:^|[ \t]+)#+[ \t]*$", "", heading.group(2) or "").strip(" \t")
            marks.append((start, index + 1, len(heading.group(1)), title))
    if not marks:
        marks = [(0, 1, 0, "(全文)")]
    elif marks[0][0] > 0:
        marks.insert(0, (0, 1, 0, "(开头)"))
    chunks = []
    for index, (start, start_line, level, title) in enumerate(marks):
        if index + 1 < len(marks):
            end, next_line = marks[index + 1][:2]
            end_line = next_line - 1
        else:
            end, end_line = len(raw), max(1, len(lines))
        chunk = raw[start:end]
        text = chunk.decode("utf-8-sig" if start == 0 else "utf-8")
        chunks.append({
            "id": content_version((version + ":" + str(start) + ":" + str(end)).encode("ascii")),
            "title": title, "level": level, "startByte": start, "endByte": end,
            "text": text, "hash": content_version(chunk),
            "start_line": start_line, "end_line": end_line,
            "chars": len(text), "editable": True,
            "optimizable": bool(level) or (title == "(全文)" and not has_frontmatter),
        })
    return {"version": version, "sections": chunks}


def parse_sections(text: str) -> list:
    """Compatibility entry point, using the same parser as byte snapshots.

    Retains the legacy title/level/line/text/chars keys, adding snapshot fields.
    Unlike the old line-join parser, text includes the actual trailing separator.
    """
    if not isinstance(text, str):
        raise TypeError("text must be str")
    try:
        raw = text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise InvalidUTF8("Text cannot be encoded as valid UTF-8.") from error
    return snapshot(raw)["sections"]


def replace_section(raw: bytes, base_version: str, section_id: str, text: str) -> bytes:
    """Replace a server-parsed chunk; no filesystem or path authorization here.

    No-op replacements preserve all bytes, even mixed newlines. Changed text
    uses the selected chunk's first newline style (or its preceding separator,
    then LF). Missing non-final separators are restored from the old boundary.
    The original BOM and all bytes outside the chunk are retained. A final
    chunk's trailing-newline presence follows the explicitly submitted text.
    Manual replacement is allowed even when ``optimizable`` is false.
    """
    if not isinstance(text, str):
        raise TypeError("text must be str")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise InvalidUTF8("Replacement text cannot be encoded as valid UTF-8.") from error
    doc = snapshot(raw)
    if base_version != doc["version"]:
        raise Conflict("File version changed or missing; refresh before saving the section.")
    section = next((s for s in doc["sections"] if s["id"] == section_id), None)
    if section is None:
        raise Conflict("Unknown or stale section ID; refresh the section list before saving.")
    start, end = section["startByte"], section["endByte"]
    if text == section["text"]:
        return raw  # Especially important for unchanged chunks with mixed EOLs.
    original = raw[start:end]
    newline_match = re.search(rb"\r\n|\n|\r", original)
    if newline_match is None:
        newline_match = re.search(rb"(\r\n|\n|\r)\Z", raw[:start])
    newline = newline_match.group().decode("ascii") if newline_match else "\n"
    replacement = re.sub(r"\r\n|\n|\r", lambda match: newline, text)
    if end < len(raw) and not replacement.endswith(("\r", "\n")):
        boundary = re.search(rb"(\r\n|\n|\r)\Z", original)
        replacement += boundary.group().decode("ascii") if boundary else newline
    bom = b"\xef\xbb\xbf" if start == 0 and original.startswith(b"\xef\xbb\xbf") else b""
    if bom and replacement.startswith("\ufeff"):
        replacement = replacement[1:]
    return raw[:start] + bom + replacement.encode("utf-8") + raw[end:]
