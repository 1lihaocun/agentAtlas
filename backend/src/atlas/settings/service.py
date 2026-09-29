import copy
import threading
from urllib.parse import urlsplit

from atlas.core.model_env import ModelEnvironment

CATEGORIES = ("instruction", "memory", "skill", "reference", "command", "hook",
              "config", "session", "log", "other")
DEFAULTS = {
    "baseUrl": "", "model": "", "dimensions": None, "batchSize": 16,
    "categories": ["instruction", "memory", "skill", "reference", "command", "hook"],
}
ENV_KEYS = {
    "baseUrl": "ATLAS_EMBEDDING_BASE_URL", "model": "ATLAS_EMBEDDING_MODEL",
    "dimensions": "ATLAS_EMBEDDING_DIMENSIONS", "batchSize": "ATLAS_EMBEDDING_BATCH_SIZE",
    "categories": "ATLAS_EMBEDDING_CATEGORIES", "apiKey": "ATLAS_EMBEDDING_API_KEY",
}


class SettingsStore:
    def __init__(self, workspace):
        self.env = ModelEnvironment(workspace)
        self.path = self.env.path
        self._lock = threading.RLock()

    def _load(self):
        values = copy.deepcopy(DEFAULTS)
        env = self.env.read()
        values.update({key: env[name] for key, name in ENV_KEYS.items() if name in env})
        if isinstance(values["categories"], str):
            values["categories"] = [item.strip() for item in values["categories"].split(",") if item.strip()]
        values.setdefault("apiKey", "")
        try:
            return self._validate(values)
        except ValueError as error:
            raise ValueError(f"模型配置 {self.path} 无效：{error}；请修复文件后重试") from error

    def public(self):
        with self._lock:
            values = self._load()
            values["apiKeyConfigured"] = bool(values.pop("apiKey"))
            return values

    def private(self):
        with self._lock:
            return self._load()

    def authorized(self):
        """Freeze consent; later writes revoke it, never broaden an in-flight job.

        The guard is transient and must run immediately before each HTTP request.
        Already dispatched requests cannot be recalled. File revisions also catch
        changes made through another service instance, even change-and-revert.
        """
        with self._lock:
            revision = self.env.revision()
            values = self.private()

            def authorize():
                with self._lock:
                    self.env.check_revision(revision)

            authorize()
            values["_authorize"] = authorize
            return values

    @staticmethod
    def _validate(values):
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
                    any(c.isspace() or ord(c) < 32 or c == "\u200b" for c in values["baseUrl"])):
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
        key = values["apiKey"]
        if len(key) > 8192 or "\n" in key or "\r" in key:
            raise ValueError("API Key 格式不正确")
        values["apiKey"] = key.strip()
        return values
