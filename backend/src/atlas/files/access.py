from contextlib import contextmanager
import os
from pathlib import Path
import stat

from atlas.assets.policy import is_sensitive_path


@contextmanager
def open_regular(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or is_sensitive_path(path):
        raise PermissionError("文件路径受限")
    descriptors = []
    stream = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptors.append(os.open(path.anchor, flags))
        for part in path.parts[1:-1]:
            descriptors.append(os.open(part, flags, dir_fd=descriptors[-1]))
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=descriptors[-1])
        descriptors.append(fd)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise PermissionError("仅允许读取普通文件")
        stream = os.fdopen(fd, "rb")
        descriptors.pop()
        yield stream
    finally:
        if stream is not None:
            stream.close()
        for descriptor in reversed(descriptors):
            os.close(descriptor)
