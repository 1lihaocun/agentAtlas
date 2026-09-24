"""Embedding preferences; public settings never contain credentials."""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
from urllib.parse import urlsplit

CATEGORIES = ("instruction", "memory", "skill", "reference", "command", "hook",
              "config", "session", "log", "other")
DEFAULTS = {
    "baseUrl": "", "model": "", "dimensions": None, "batchSize": 16,
    "categories": ["instruction", "memory", "skill", "reference", "command", "hook"],
}


def atomic_write(path, content, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".atlas-", dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class SettingsStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "settings.json"
        self.key_path = self.directory / "embedding-key"
        self._lock = threading.RLock()

    def _load(self):
        values = copy.deepcopy(DEFAULTS)
        if self.path.exists():
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            values.update({key: stored[key] for key in DEFAULTS if key in stored})
        return values

    def public(self):
        with self._lock:
            values = self._load()
            values["apiKeyConfigured"] = self.key_path.is_file() and self.key_path.stat().st_size > 0
            return values

    def private(self):
        with self._lock:
            values = self._load()
            values["apiKey"] = self.key_path.read_text(encoding="utf-8") if self.key_path.is_file() else ""
            return values

    def _revision(self):
        revision = []
        for path in (self.path, self.key_path):
            try:
                info = path.stat()
                revision.append((info.st_dev, info.st_ino, info.st_mtime_ns,
                                 info.st_ctime_ns, info.st_size))
            except FileNotFoundError:
                revision.append(None)
        return tuple(revision)

    def authorized(self):
        """Freeze consent; later writes revoke it, never broaden an in-flight job.

        The guard is transient and must run immediately before each HTTP request.
        Already dispatched requests cannot be recalled. File revisions also catch
        changes made through another service instance, even change-and-revert.
        """
        with self._lock:
            revision = self._revision()
            values = self.private()

            def authorize():
                with self._lock:
                    if self._revision() != revision:
                        raise ValueError("云端授权或配置已变更，已停止后续请求；请重新确认")

            authorize()
            values["_authorize"] = authorize
            return values

    def save(self, changes):
        if not isinstance(changes, dict):
            raise ValueError("配置必须是 JSON 对象")
        with self._lock:
            values = self._load()
            values.update({key: changes[key] for key in DEFAULTS if key in changes})
            for key in ("baseUrl", "model"):
                if not isinstance(values[key], str) or len(values[key]) > 2048:
                    raise ValueError("服务地址和模型名必须是文本")
                values[key] = values[key].strip().rstrip("/") if key == "baseUrl" else values[key].strip()
            if values["baseUrl"]:
                try:
                    url = urlsplit(values["baseUrl"])
                    port = url.port
                except ValueError:
                    raise ValueError("服务地址格式不正确") from None
                local = url.hostname in ("localhost", "127.0.0.1", "::1")
                if (url.scheme not in ("http", "https") or not url.hostname or
                        (url.scheme == "http" and not local) or url.username or
                        url.password or url.query or url.fragment or "\\" in values["baseUrl"] or
                        any(ord(c) < 32 for c in values["baseUrl"])):
                    raise ValueError("云端服务必须使用 HTTPS，地址不能携带凭证或查询参数")
            for key, low, high in (("dimensions", 1, 65536), ("batchSize", 1, 128)):
                value = values[key]
                if key == "dimensions" and value in (None, ""):
                    values[key] = None
                    continue
                if isinstance(value, bool) or not isinstance(value, (str, int)):
                    raise ValueError("%s 必须为正整数" % key)
                try:
                    parsed = int(value)
                except (ValueError, TypeError):
                    raise ValueError("%s 必须为正整数" % key) from None
                if not low <= parsed <= high:
                    raise ValueError("%s 超出允许范围" % key)
                values[key] = parsed
            categories = values["categories"]
            if not isinstance(categories, list) or any(c not in CATEGORIES for c in categories):
                raise ValueError("文件类别不正确")
            values["categories"] = list(dict.fromkeys(categories))
            key = changes.get("apiKey", "")
            if not isinstance(key, str) or len(key) > 8192 or "\n" in key or "\r" in key:
                raise ValueError("API Key 格式不正确")
            self.directory.mkdir(parents=True, exist_ok=True)
            os.chmod(self.directory, 0o700)
            if changes.get("clearApiKey") is True:
                self.key_path.unlink(missing_ok=True)
            elif key.strip():
                atomic_write(self.key_path, key.strip().encode("utf-8"))
            atomic_write(self.path, json.dumps(values, ensure_ascii=False, indent=2).encode("utf-8"))
            return self.public()
