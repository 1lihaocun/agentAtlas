import hashlib
import os
from pathlib import Path
import stat
import threading
import time


from atlas.assets.catalog import read_text, is_sensitive_path
from atlas.core.storage import Store, StorageProblem

MAX_FILE_BYTES = 2 * 1024 * 1024


class FileProblem(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class FileStore:
    def __init__(self, entries, backup_dir):
        self.entries = entries
        self.backup_dir = Path(backup_dir)
        self.lock = threading.RLock()
        self._journal = None

    def entry(self, path, write=False):
        if not isinstance(path, str) or not path or "\x00" in path:
            raise FileProblem("文件路径无效")
        full = os.path.abspath(path)
        if full != os.path.realpath(full) or is_sensitive_path(full):
            raise FileProblem("不允许访问已改变指向的符号链接", 403)
        record = next((f for f in self.entries() if f["path"] == full), None)
        if record is None or record.get("restricted") or record.get("searchable") is False:
            raise FileProblem("文件不在可访问的扫描索引中", 403)
        if write and not record.get("editable", False):
            raise FileProblem(record.get("reason") or "此类文件仅允许查看，不允许修改", 403)
        return record

    def _raw(self, path, max_bytes):
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise FileProblem("仅支持普通文本文件", 403)
                raw = stream.read(max_bytes + 1)
        except FileNotFoundError:
            raise FileProblem("文件不存在；请重新扫描", 404) from None
        except OSError:
            raise FileProblem("无法读取文件", 403) from None
        return raw, info

    def read(self, path, max_bytes=MAX_FILE_BYTES):
        record = self.entry(path)
        full = record["path"]
        try:
            data = read_text(dict(record, searchable=True), max_bytes=max_bytes)
        except PermissionError:
            raise FileProblem("文件路径或内容包含敏感配置，不允许查看", 403) from None
        except FileNotFoundError:
            raise FileProblem("文件不存在；请重新扫描", 404) from None
        except ValueError:
            raise FileProblem("文件不是可预览的文本", 415) from None
        except OSError:
            raise FileProblem("文件读取失败或正在变化，请重试", 409) from None
        truncated = data["truncated"]
        damaged = data.get("decodeReplaced", False)
        editable = bool(record.get("editable")) and not truncated and not damaged
        return {
            "path": full, "content": data["content"], "mtime": data["mtime"],
            "mtimeNs": data["mtimeNs"], "bytes": data["bytes"],
            "sha256": None if truncated or damaged else data["sha256"],
            "editable": editable, "category": record.get("category", "instruction"),
            "truncated": truncated, "previewBytes": data["readBytes"],
            "readOnlyReason": ("文件较大，仅预览前 2 MiB；禁止保存截断内容" if truncated else
                               "编码包含无效 UTF-8 字节，仅供查看" if damaged else
                               (record.get("reason") or "此类文件只读") if not editable else ""),
        }

    def save(self, path, content, expected_hash, source="file"):
        self.entry(path, write=True)
        if not isinstance(content, str):
            raise FileProblem("文件内容必须是文本")
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
            raise FileProblem("缺少原文件版本；请重新打开文件后保存", 409)
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            raise FileProblem("编辑内容不能超过 2 MiB", 413)
        with self.lock:
            record = self.entry(path, write=True)
            full = record["path"]
            old, info = self._raw(full, MAX_FILE_BYTES)
            if len(old) > MAX_FILE_BYTES:
                raise FileProblem("大文件只读，不能保存预览内容", 403)
            if hashlib.sha256(old).hexdigest() != expected_hash:
                raise FileProblem("文件已被外部修改，请重新打开后再保存", 409)
            if self._journal is None:
                self._journal = Store(self.backup_dir.parent / "lifecycle")
            try:
                result = self._journal.save(full, expected_hash, encoded, [full], source, time.time())
            except StorageProblem as exc:
                problem = FileProblem(str(exc), exc.status)
                problem.code = exc.code
                problem.changed = exc.code == "write_committed_audit_pending"
                raise problem from exc
            return dict(result, sha256=result["version"], bytes=len(encoded),
                        mtime=os.stat(full).st_mtime)
