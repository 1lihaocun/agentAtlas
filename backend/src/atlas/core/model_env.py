from io import StringIO
from pathlib import Path

from dotenv.parser import parse_stream


class ModelEnvironment:
    """Model configuration belongs to the selected workspace, not the host agent.

    Values are literal: no shell expansion or process-environment inheritance.
    This also prevents credentials containing ${...} from being rewritten.
    """

    def __init__(self, workspace):
        self.path = Path(workspace) / ".env"

    def _bindings(self):
        try:
            content = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except (OSError, UnicodeError) as error:
            raise ValueError(f"模型配置 {self.path} 无法读取；请修复文件后重试") from error
        bindings = list(parse_stream(StringIO(content)))
        if any(item.error or (item.key is not None and item.value is None) for item in bindings):
            raise ValueError(f"模型配置 {self.path} 格式不正确；请修复文件后重试")
        return bindings

    def read(self):
        return {item.key: item.value for item in self._bindings()
                if item.key is not None and item.value is not None}

    def revision(self):
        try:
            info = self.path.stat()
        except FileNotFoundError:
            return None
        return (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size)

    def check_revision(self, revision):
        if self.revision() != revision:
            raise ValueError("云端授权或配置已变更，已停止操作；请重新读取配置并确认")
