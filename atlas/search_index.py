"""Local SQLite search. Network operations are explicitly opt-in.

Line ranges are 1-based and inclusive. A SearchIndex opens a fresh SQLite
connection per operation; no caller-owned connection crosses worker threads.

Keyword queries are literal, case-insensitive phrases, not an FTS operator
language. Search totals count matching chunks before the result limit. Vector
counts describe cached (provider signature, unique text) pairs; embeddedChunks
counts live chunks covered by any cached provider signature. Provider settings
are transient and never stored here. Real cloud use requires caller-supplied
configuration and explicit document-build consent outside this module.
"""
from contextlib import contextmanager
from http.client import HTTPException
import hashlib
import ipaddress
import json
import math
import os
import re
import sqlite3
import struct
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
CHUNK_CHARS = 1800
SNIPPET_CHARS = 420
HTTP_TIMEOUT = 30
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class SearchIndexError(ValueError):
    """Safe to display to callers: messages never contain provider bodies or keys."""


class EmbeddingError(SearchIndexError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _configuration(settings):
    if not isinstance(settings, dict):
        raise EmbeddingError("Embedding provider is not configured.")
    base, model, key = (settings.get(k) for k in ("baseUrl", "model", "apiKey"))
    if not all(isinstance(v, str) and v.strip() for v in (base, model, key)):
        raise EmbeddingError("Embedding provider is not configured.")
    try:
        parsed = urlsplit(base)
        host = parsed.hostname
        parsed.port  # Validate malformed/out-of-range ports without echoing the URL.
        if not host or parsed.username is not None or parsed.password is not None:
            raise ValueError()
        if parsed.query or parsed.fragment or any(c.isspace() or ord(c) < 32 for c in base):
            raise ValueError()
        loopback = host.lower() == "localhost"
        try:
            loopback = loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
            raise ValueError()
        path = parsed.path.rstrip("/") or "/v1"
        if not path.endswith("/embeddings"):
            path += "/embeddings"
        endpoint = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    except (TypeError, ValueError):
        raise EmbeddingError("Embedding URL must use HTTPS or loopback HTTP, without credentials or query parameters.") from None
    dimensions = settings.get("dimensions")
    if dimensions is not None and (type(dimensions) is not int or dimensions <= 0):
        raise EmbeddingError("Embedding dimensions must be a positive integer.")
    batch_size = settings.get("batchSize", 32)
    if type(batch_size) is not int or not 1 <= batch_size <= 256:
        raise EmbeddingError("Embedding batch size must be between 1 and 256.")
    categories = settings.get("categories")
    if categories is not None and (not isinstance(categories, list) or
                                  not all(isinstance(c, str) for c in categories)):
        raise EmbeddingError("Embedding categories must be a list of strings.")
    if any(ord(c) < 32 or ord(c) == 127 for c in key):
        raise EmbeddingError("Embedding API key is invalid.")
    signature = hashlib.sha256(json.dumps([endpoint, model, dimensions],
                                         ensure_ascii=False).encode("utf-8")).hexdigest()
    return dict(endpoint=endpoint, model=model, apiKey=key, dimensions=dimensions,
                signature=signature, batchSize=batch_size, categories=categories,
                _authorize=settings.get("_authorize"))


def _normal_vector(vector, dimensions=None):
    if not isinstance(vector, list) or not vector or (dimensions and len(vector) != dimensions):
        raise EmbeddingError("Embedding provider returned invalid vector dimensions.")
    if not all(type(n) in (int, float) for n in vector):
        raise EmbeddingError("Embedding provider returned an invalid vector.")
    try:
        vector = [float(n) for n in vector]
        if not all(math.isfinite(n) for n in vector):
            raise ValueError()
        scale = max(abs(n) for n in vector)
        if not scale:
            raise ValueError()
        scaled = [n / scale for n in vector]
        norm = math.sqrt(math.fsum(n * n for n in scaled))
        return [n / norm for n in scaled]
    except (OverflowError, ValueError):
        raise EmbeddingError("Embedding provider returned a non-finite or zero vector.") from None


def _request_embeddings(config, texts, dimensions=None):
    payload = {"model": config["model"], "input": texts, "encoding_format": "float"}
    if config["dimensions"] is not None:
        payload["dimensions"] = config["dimensions"]
    if config.get("_authorize") is not None:
        try:
            config["_authorize"]()
        except ValueError:
            raise EmbeddingError("云端授权或配置已变更，已停止后续请求；请重新确认") from None
    try:
        request = Request(config["endpoint"], data=json.dumps(payload).encode("utf-8"),
                          headers={"Authorization": "Bearer " + config["apiKey"],
                                   "Content-Type": "application/json", "Accept": "application/json",
                                   "User-Agent": "curl/8.0 AgentAtlas/1.0"}, method="POST")
        # Never forward Authorization on redirects, nor send loopback traffic to a proxy.
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        with opener.open(request, timeout=HTTP_TIMEOUT) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise EmbeddingError("Embedding provider response is too large.")
    except HTTPError as exc:
        code = exc.code
        exc.close()
        raise EmbeddingError("Embedding provider request failed (HTTP %s)." % code) from None
    except (URLError, OSError, ValueError, HTTPException):
        raise EmbeddingError("Embedding provider request failed.") from None
    try:
        data = json.loads(raw)["data"]
        if not isinstance(data, list) or len(data) != len(texts):
            raise ValueError()
        result = [None] * len(texts)
        expected = dimensions or config["dimensions"]
        for item in data:
            index = item["index"]
            if type(index) is not int or not 0 <= index < len(texts) or result[index] is not None:
                raise ValueError()
            vector = _normal_vector(item["embedding"], expected)
            expected = len(vector)
            result[index] = vector
        if any(v is None for v in result):
            raise ValueError()
        return result
    except EmbeddingError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise EmbeddingError("Embedding provider returned an invalid response.") from None


def _eligible(config):
    if config["categories"] is None:
        return "lower(f.category) NOT IN ('config','session','log','sessions','logs')", []
    return _where({"categories": config["categories"]})


def _chunks(text):
    """Paragraph/line chunks, with overlapping slices for unusually long lines."""
    parts, size, start, end = [], 0, 1, 1
    for line_number, line in enumerate(text.splitlines(keepends=True), 1):
        offset = 0
        while offset < len(line):
            piece = line[offset:offset + CHUNK_CHARS]
            if parts and size + len(piece) > CHUNK_CHARS:
                yield start, end, "".join(parts)
                parts, size = [], 0
            if not parts:
                start = line_number
            parts.append(piece)
            size += len(piece)
            end = line_number
            if offset + CHUNK_CHARS >= len(line):
                break
            # Overlap preserves identifiers/words straddling a long-line split.
            offset += CHUNK_CHARS - 128
        if not line.strip() and size >= CHUNK_CHARS // 2:
            yield start, end, "".join(parts)
            parts, size = [], 0
    if parts:
        yield start, end, "".join(parts)
    elif not text:
        yield 1, 1, ""


def _result(row, query, match_type):
    text = row["text"]
    match = re.search(re.escape(query), text, re.IGNORECASE) if query else None
    begin = max(0, match.start() - 100) if match else 0
    snippet = text[begin:begin + SNIPPET_CHARS].rstrip("\r\n")
    start_line = row["start_line"] + text[:begin].count("\n")
    return {"path": row["path"], "name": row["name"], "platform": row["platform"],
            "category": row["category"], "project": row["project"],
            "startLine": start_line, "endLine": start_line + snippet.count("\n"),
            "snippet": snippet, "score": row["score"], "matchType": match_type}


def _where(filters):
    if filters is not None and not isinstance(filters, dict):
        raise SearchIndexError("Search filters must be an object.")
    clauses, values = [], []
    for field, value in (filters or {}).items():
        if field not in ("platform", "category", "project", "path", "categories"):
            raise ValueError("Unknown search filter")
        if value is None or value == "":
            continue
        column = "category" if field == "categories" else field
        if isinstance(value, (list, tuple)):
            if not all(isinstance(v, str) for v in value):
                raise ValueError("Invalid search filter")
            clauses.append("f.%s IN (%s)" % (column, ",".join("?" for _ in value))
                           if value else "0")
            values.extend(value)
        elif isinstance(value, str):
            clauses.append("f.%s=?" % column)
            values.append(value)
        else:
            raise ValueError("Invalid search filter")
    return " AND ".join(clauses) or "1", values


class SearchIndex:
    def __init__(self, db_path):
        self.db_path = os.path.abspath(os.fspath(db_path))
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(self.db_path, threading.RLock())
        with self._lock, self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY, name TEXT NOT NULL,
                    platform TEXT NOT NULL, category TEXT NOT NULL,
                    project TEXT NOT NULL, sha256 TEXT NOT NULL,
                    mtime REAL, bytes INTEGER, truncated INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL, preview_sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY, path TEXT NOT NULL REFERENCES files(path)
                        ON DELETE CASCADE,
                    start_line INTEGER NOT NULL, end_line INTEGER NOT NULL,
                    text TEXT NOT NULL, text_sha256 TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
                CREATE INDEX IF NOT EXISTS chunks_hash ON chunks(text_sha256);
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    name, path, text, tokenize='trigram'
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vocab USING fts5vocab(chunks_fts, row);
                CREATE INDEX IF NOT EXISTS chunks_text_tail ON chunks(substr(text,-2));
                CREATE TRIGGER IF NOT EXISTS chunks_delete AFTER DELETE ON chunks
                BEGIN DELETE FROM chunks_fts WHERE rowid=old.id; END;
                CREATE TABLE IF NOT EXISTS profiles (
                    signature TEXT PRIMARY KEY, dimensions INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS vectors (
                    signature TEXT NOT NULL REFERENCES profiles(signature),
                    text_sha256 TEXT NOT NULL, vector BLOB NOT NULL,
                    PRIMARY KEY(signature,text_sha256)
                );
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.create_function("atlas_nonempty", 1, lambda text: bool(text and text.strip()), deterministic=True)
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _stats(db):
        if not db.in_transaction:
            db.execute("BEGIN")  # All counters must describe the same WAL snapshot.
        truncated_paths = [r[0] for r in db.execute("SELECT path FROM files WHERE truncated=1 ORDER BY path")]
        return {"files": db.execute("SELECT count(*) FROM files").fetchone()[0],
                "chunks": db.execute("SELECT count(*) FROM chunks").fetchone()[0],
                "vectors": db.execute("SELECT count(*) FROM vectors").fetchone()[0],
                "embeddedChunks": db.execute("SELECT count(*) FROM chunks WHERE text_sha256 IN "
                    "(SELECT text_sha256 FROM vectors)").fetchone()[0],
                "profiles": db.execute("SELECT count(*) FROM profiles").fetchone()[0],
                "truncatedFiles": len(truncated_paths), "truncatedPaths": truncated_paths}

    def status(self):
        with self._connect() as db:
            return self._stats(db)

    def sync(self, files, reader):
        """Synchronize a complete snapshot. This method never contacts a provider."""
        # Apply restrictions before fingerprint reuse: a policy change need not
        # change source bytes. Omitted entries cascade-delete chunks and FTS rows.
        entries = {entry["path"]: entry for entry in files
                   if entry.get("searchable", True) is not False and not entry.get("restricted")}
        counts = dict(added=0, updated=0, unchanged=0, deleted=0, errors=0)
        failed_paths = []
        with self._lock, self._connect() as db:
            existing = {r["path"]: r for r in db.execute("SELECT * FROM files")}
            vanished = existing.keys() - entries.keys()
            db.executemany("DELETE FROM files WHERE path=?", [(p,) for p in vanished])
            counts["deleted"] = len(vanished)
            for path, entry in entries.items():
                old = existing.get(path)
                fingerprint = self._fingerprint(entry)
                name = entry.get("name") or os.path.basename(path)
                metadata = (name, entry.get("platform", ""), entry.get("category", ""),
                            entry.get("project", entry.get("proj", entry.get("root", ""))))
                if old is not None and fingerprint and old["fingerprint"] == fingerprint:
                    db.execute("UPDATE files SET name=?,platform=?,category=?,project=? "
                               "WHERE path=?", metadata + (path,))
                    if old["name"] != name:
                        db.execute("UPDATE chunks_fts SET name=? WHERE rowid IN "
                                   "(SELECT id FROM chunks WHERE path=?)", (name, path))
                    counts["unchanged"] += 1
                    continue
                try:
                    data = reader(entry)
                    text = data["content"]
                    if not isinstance(text, str):
                        raise ValueError()
                    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                except (OSError, ValueError, TypeError, KeyError):
                    # Unreadable previews must not leave a stale searchable copy.
                    db.execute("DELETE FROM files WHERE path=?", (path,))
                    counts["errors"] += 1
                    failed_paths.append(path)
                    continue
                db.execute("""
                    INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(path) DO UPDATE SET name=excluded.name,
                      platform=excluded.platform,category=excluded.category,
                      project=excluded.project,sha256=excluded.sha256,
                      mtime=excluded.mtime,bytes=excluded.bytes,
                      truncated=excluded.truncated,fingerprint=excluded.fingerprint,
                      preview_sha256=excluded.preview_sha256
                """, (path,) + metadata + (data.get("sha256", digest), data.get("mtime"),
                      data.get("bytes", len(text.encode("utf-8"))),
                      bool(data.get("truncated")), fingerprint, digest))
                if old is None or old["preview_sha256"] != digest:
                    db.execute("DELETE FROM chunks WHERE path=?", (path,))
                    for start_line, end_line, chunk in _chunks(text):
                        chunk_id = db.execute(
                            "INSERT INTO chunks(path,start_line,end_line,text,text_sha256) "
                            "VALUES (?,?,?,?,?)", (path, start_line, end_line, chunk,
                            hashlib.sha256(chunk.encode("utf-8")).hexdigest())).lastrowid
                        db.execute("INSERT INTO chunks_fts(rowid,name,path,text) VALUES (?,?,?,?)",
                                   (chunk_id, name, path, chunk))
                elif old["name"] != name:
                    db.execute("UPDATE chunks_fts SET name=? WHERE rowid IN "
                               "(SELECT id FROM chunks WHERE path=?)", (name, path))
                counts["added" if old is None else "updated"] += 1
            db.execute("DELETE FROM vectors WHERE NOT EXISTS "
                       "(SELECT 1 FROM chunks c WHERE c.text_sha256=vectors.text_sha256)")
            return dict(self._stats(db), failedPaths=failed_paths, **counts)

    @staticmethod
    def _fingerprint(entry):
        source_sha = entry.get("sha256", entry.get("sha1"))
        try:
            st = os.stat(entry["path"])
            return json.dumps([st.st_mtime_ns, st.st_size, st.st_ctime_ns, source_sha])
        except OSError:
            if "mtime" in entry and "bytes" in entry:
                return json.dumps([entry["mtime"], entry["bytes"], source_sha])
            return ""

    def search(self, query, mode="keyword", filters=None, limit=50, settings=None):
        if not isinstance(query, str) or "\x00" in query or len(query) > 4096:
            raise SearchIndexError("Search query must be text of at most 4096 characters without NULs.")
        if mode not in ("keyword", "vector", "hybrid"):
            raise SearchIndexError("Search mode must be keyword, vector, or hybrid.")
        if type(limit) is not int or limit < 0:
            raise SearchIndexError("Search limit must be a nonnegative integer.")
        _where(filters)
        query = query.strip()
        if not query.strip():
            return {"results": [], "total": 0, "mode": mode}
        if mode == "keyword":
            rows, total = self._keyword_search(query, filters, limit=limit, with_total=True)
            return {"results": [_result(row, query, "keyword") for row in rows], "total": total, "mode": mode}
        warning = None
        if mode == "vector":
            rows = self._vector_search(query, filters, settings)
            results = [_result(r, query, "vector") for r in rows]
        else:
            try:
                vector_rows = self._vector_search(query, filters, settings)
            except EmbeddingError as exc:
                answer = self.search(query, mode="keyword", filters=filters, limit=limit)
                answer.update(mode=mode, warning="Vector search unavailable; showing keyword results. " + str(exc))
                return answer
            rows = self._keyword_search(query, filters)
            fused = {}
            for source, ranked in (("keyword", rows), ("vector", vector_rows)):
                for rank, row in enumerate(ranked, 1):
                    key = (row["path"], row["text_sha256"], row["start_line"], row["end_line"])
                    if key not in fused:
                        fused[key] = [row, 0.0, set()]
                    fused[key][1] += 1.0 / (60 + rank)
                    fused[key][2].add(source)
            results = [_result(dict(row, score=score), query,
                               "hybrid" if len(sources) == 2 else next(iter(sources)))
                       for row, score, sources in fused.values()]
            results.sort(key=lambda r: (-r["score"], r["path"], r["startLine"]))
        answer = {"results": results[:limit], "total": len(results), "mode": mode}
        if warning:
            answer["warning"] = warning
        return answer

    def _keyword_search(self, query, filters, limit=None, with_total=False):
        phrase = '"' + query.replace('"', '""') + '"'
        where, params = _where(filters)
        suffix, limit_params = (" LIMIT ?", [limit]) if limit is not None else ("", [])
        with self._connect() as db:
            db.execute("BEGIN")
            if re.fullmatch(r"[\u3400-\u9fff]{2}", query):
                # A trigram starting with a two-character Han query covers every
                # occurrence except the final two characters of a column. Range
                # lookup in the vocabulary plus indexed column tails avoids two
                # scans of the entire text store, without dropping short files.
                terms = [r[0] for r in db.execute(
                    "SELECT term FROM chunks_vocab WHERE term>=? AND term<? LIMIT 2049",
                    (query, query + chr(0x10ffff)))]
                if len(terms) <= 2048:
                    candidates = ["SELECT id FROM chunks WHERE substr(text,-2)=?",
                                  "SELECT id FROM chunks WHERE path IN "
                                  "(SELECT path FROM files WHERE substr(name,-2)=? OR substr(path,-2)=?)"]
                    candidate_params = [query, query, query]
                    if terms:
                        candidates.append("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?")
                        candidate_params.append(" OR ".join('"' + term.replace('"', '""') + '"' for term in terms))
                    where += " AND c.id IN (" + " UNION ".join(candidates) + ")"
                    params += candidate_params
            total = None
            if with_total:
                if len(query) < 3:
                    total = db.execute("SELECT count(*) FROM chunks c JOIN files f ON f.path=c.path "
                        "WHERE (instr(lower(f.name),lower(?)) OR instr(lower(f.path),lower(?)) "
                        "OR instr(lower(c.text),lower(?))) AND " + where, [query, query, query] + params).fetchone()[0]
                else:
                    total = db.execute("SELECT count(*) FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid "
                        "JOIN files f ON f.path=c.path WHERE chunks_fts MATCH ? AND " + where, [phrase] + params).fetchone()[0]
            if len(query) < 3:
                rows = db.execute("""
                    SELECT c.*, f.name, f.platform, f.category, f.project,
                        1.0 + 8*(instr(lower(f.name),lower(?))>0)
                            + 4*(instr(lower(f.path),lower(?))>0) AS score
                    FROM chunks c JOIN files f ON f.path=c.path
                    WHERE (instr(lower(f.name),lower(?)) OR instr(lower(f.path),lower(?))
                       OR instr(lower(c.text),lower(?))) AND """ + where + """
                    ORDER BY score DESC,c.path,c.start_line
                """ + suffix, [query, query, query, query, query] + params + limit_params).fetchall()
            else:
                rows = db.execute("""
                    SELECT c.*, f.name, f.platform, f.category, f.project,
                           -bm25(chunks_fts, 8, 4, 1) AS score
                    FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid
                    JOIN files f ON f.path=c.path WHERE chunks_fts MATCH ? AND """ + where + """
                    ORDER BY score DESC, c.path, c.start_line
                """ + suffix, [phrase] + params + limit_params).fetchall()
        return (rows, total) if with_total else rows

    def _pending(self, db, config, limit=None, count_only=False):
        where, params = _eligible(config)
        source = """ FROM chunks c
            JOIN files f ON f.path=c.path
            LEFT JOIN vectors v ON v.text_sha256=c.text_sha256 AND v.signature=?
            WHERE v.text_sha256 IS NULL AND atlas_nonempty(c.text) AND """ + where
        values = [config["signature"]] + params
        if count_only:
            return db.execute("SELECT count(DISTINCT c.text_sha256)" + source, values).fetchone()[0]
        sql = "SELECT c.text_sha256, min(c.text) AS text" + source + " GROUP BY c.text_sha256 ORDER BY min(c.path),min(c.id)"
        if limit is not None:
            sql += " LIMIT ?"
            values.append(limit)
        return db.execute(sql, values).fetchall()

    def embed_pending(self, settings, max_chunks=256, progress=None):
        """The sole document-upload entry point. Batches commit only after validation.

        Counts refer to unique chunk texts; repeated files reuse the same vector.
        ``pending`` reports remaining unique texts, so the work cap is never silent.
        ``progress`` receives a stats dict after each successfully committed batch.
        """
        config = _configuration(settings)
        if type(max_chunks) is not int or max_chunks < 0:
            raise EmbeddingError("max_chunks must be a nonnegative integer.")
        with self._connect() as db:
            pending = self._pending(db, config, limit=max_chunks)
            profile = db.execute("SELECT dimensions FROM profiles WHERE signature=?",
                                 (config["signature"],)).fetchone()
        dimensions = profile[0] if profile else config["dimensions"]
        embedded, batches = 0, 0
        for start in range(0, len(pending), config["batchSize"]):
            batch = pending[start:start + config["batchSize"]]
            # Recheck opt-in and existence before EACH upload, not just job start.
            eligible, category_params = _eligible(config)
            with self._connect() as db:
                live_hashes = {r[0] for r in db.execute("""
                    SELECT DISTINCT c.text_sha256 FROM chunks c JOIN files f ON f.path=c.path
                    LEFT JOIN vectors v ON v.text_sha256=c.text_sha256 AND v.signature=?
                    WHERE v.text_sha256 IS NULL AND c.text_sha256 IN (""" +
                    ",".join("?" for _ in batch) + ") AND " + eligible,
                    [config["signature"]] + [r["text_sha256"] for r in batch] + category_params)}
            batch = [r for r in batch if r["text_sha256"] in live_hashes]
            if not batch:
                continue
            vectors = _request_embeddings(config, [r["text"] for r in batch], dimensions)
            dimensions = len(vectors[0])
            with self._lock, self._connect() as db:
                db.execute("INSERT OR IGNORE INTO profiles VALUES (?,?)",
                           (config["signature"], dimensions))
                actual = db.execute("SELECT dimensions FROM profiles WHERE signature=?",
                                    (config["signature"],)).fetchone()[0]
                if actual != dimensions:
                    raise EmbeddingError("Embedding dimensions changed; use a different model setting.")
                eligible, category_params = _eligible(config)
                for row, vector in zip(batch, vectors):
                    # Sync may remove/reclassify content while HTTP is in flight.
                    inserted = db.execute("""
                        INSERT OR IGNORE INTO vectors SELECT ?,?,? WHERE EXISTS (
                            SELECT 1 FROM chunks c JOIN files f ON f.path=c.path
                            WHERE c.text_sha256=? AND """ + eligible + ")", [
                        config["signature"], row["text_sha256"],
                        struct.pack("<%sd" % dimensions, *vector),
                        row["text_sha256"]] + category_params)
                    embedded += inserted.rowcount
            batches += 1
            if progress:
                progress({"embedded": embedded, "batches": batches})
        with self._connect() as db:
            return dict(self._stats(db), embedded=embedded, batches=batches,
                        pending=self._pending(db, config, count_only=True))

    def _vector_search(self, query, filters, settings):
        config = _configuration(settings)
        where, params = _where(filters)
        eligible, category_params = _eligible(config)
        with self._connect() as db:
            rows = db.execute("""
                SELECT c.*, f.name,f.platform,f.category,f.project,v.vector,p.dimensions
                FROM vectors v JOIN profiles p ON p.signature=v.signature
                JOIN chunks c ON c.text_sha256=v.text_sha256 JOIN files f ON f.path=c.path
                WHERE v.signature=? AND """ + where + " AND " + eligible,
                [config["signature"]] + params + category_params).fetchall()
            if not rows:
                available = db.execute("""
                    SELECT 1 FROM vectors v JOIN chunks c ON c.text_sha256=v.text_sha256
                    JOIN files f ON f.path=c.path WHERE v.signature=? AND """ + eligible + " LIMIT 1",
                    [config["signature"]] + category_params).fetchone()
                if available:
                    return []
                raise EmbeddingError("No vectors are available for these settings; build embeddings first.")
        query_vector = _request_embeddings(config, [query], rows[0]["dimensions"])[0]
        results = []
        for row in rows:
            vector = struct.unpack("<%sd" % row["dimensions"], row["vector"])
            score = max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(query_vector, vector))))
            results.append(dict(row, score=score))
        return sorted(results, key=lambda r: (-r["score"], r["path"], r["start_line"], r["id"]))
