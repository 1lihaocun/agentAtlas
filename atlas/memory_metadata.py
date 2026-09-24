"""Bounded display metadata; content associations never become loading rules."""
from pathlib import Path
from copy import deepcopy
import re
import stat
import threading

try:
    from .catalog import read_text, is_sensitive_path
    from .platforms import extension_declaration as _platform_extension_declaration
except ImportError:
    from catalog import read_text, is_sensitive_path
    from platforms import extension_declaration as _platform_extension_declaration

MAX_METADATA_BYTES = 16384


def _display(value, limit):
    value = value.strip()
    if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return ' '.join(value.split())[:limit]


def parse_memory_metadata(content, source, project_roots, truncated=False):
    lines = content.splitlines()
    fields = {}
    front_end = 0
    if lines and lines[0].strip() == '---':
        for index, line in enumerate(lines[1:65], 1):
            if line.strip() == '---':
                front_end = index + 1
                break
        if front_end:
            for line in lines[1:front_end-1]:
                match = re.match(r'^(title|description):\s*(.+)$', line)
                if match and match[2].strip() not in {'|', '>', '|-', '>-'}:
                    fields[match[1]] = _display(match[2], 160 if match[1] == 'title' else 320)
    title = fields.get('title', '')
    if not title:
        for line in lines[front_end:]:
            heading = re.match(r'^#{1,3}\s+(.+?)\s*#*\s*$', line)
            if heading:
                title = _display(heading[1], 160)
                break
    related = {}
    roots = sorted({str(root) for root in project_roots if Path(root).is_absolute()
                    and '..' not in Path(root).parts}, key=lambda root: (-len(root), root))
    if roots:
        # Longest alternatives avoid assigning a nested repository to its parent.
        pattern = re.compile(r'(?<![\w/.-])(' + '|'.join(map(re.escape, roots)) +
                             r')(?=$|[/\s`\"\'<>),;:}\]，。])(/[^\s`\"\'<>，。]*)?')
        header = True
        for number, line in enumerate(lines, 1):
            if not line.strip() or line.startswith('#') or number > 20:
                header = False
            for match in pattern.finditer(line):
                if '..' in Path(match[0].rstrip('),;:}]')).parts:
                    continue
                root = match[1]
                basis = 'source_cwd' if header and re.match(r'^cwd:\s*', line) else 'path_mention'
                if root not in related or basis == 'source_cwd':
                    related[root] = {'path': root, 'name': Path(root).name, 'basis': basis,
                                     'evidence': {'path': source, 'line': number}}
    return {'title': title or Path(source).name, 'description': fields.get('description', ''),
            'relatedProjects': [related[key] for key in sorted(related)],
            'status': 'partial' if truncated else 'ready',
            'truncated': bool(truncated)}


class MemoryMetadata:
    """Cache derived display fields only; validate live paths on every request."""
    def __init__(self, reader=None):
        self.reader = reader or read_text
        self._cache = {}
        self._lock = threading.RLock()

    def collect(self, files, project_roots):
        roots = tuple(sorted(set(map(str, project_roots))))
        result, evidence = {}, {}
        with self._lock:
            for entry in files:
                if entry.get('category') != 'memory':
                    continue
                source = entry.get('path', '')
                if source in result:
                    continue
                path = Path(source)
                empty = {'title': path.name, 'description': '', 'relatedProjects': [],
                         'status': 'unavailable', 'truncated': False}
                if not entry.get('searchable', False) or entry.get('restricted'):
                    result[source] = dict(empty, status='metadata_only')
                    self._cache.pop(source, None)
                    continue
                try:
                    if (not path.is_absolute() or '..' in path.parts or is_sensitive_path(path)
                            or path.resolve() != path):
                        raise PermissionError('restricted path')
                    info = path.lstat()
                    if not stat.S_ISREG(info.st_mode):
                        raise PermissionError('nonregular file')
                    fingerprint = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                                   info.st_ctime_ns, entry.get('platform'), entry.get('profile'), roots)
                    cached = self._cache.get(source)
                    if cached is None or cached[0] != fingerprint:
                        data = self.reader(entry, max_bytes=MAX_METADATA_BYTES)
                        if data.get('decodeReplaced') and not (data.get('truncated')
                                and data['content'].endswith('\ufffd') and '\ufffd' not in data['content'][:-1]):
                            raise ValueError('invalid UTF-8')
                        metadata = parse_memory_metadata(data['content'], source, roots,
                                                         truncated=data.get('truncated', False))
                        declaration = None
                        tail = path.parts[-5:]
                        if _platform_extension_declaration(entry.get('platform'), entry.get('profile', 'default'), tail):
                            declaration = {key: data[key] for key in ('content', 'sha256', 'truncated')}
                        cached = (fingerprint, metadata, declaration)
                        self._cache[source] = cached
                    result[source] = deepcopy(cached[1])
                    if cached[2] is not None:
                        evidence[source] = deepcopy(cached[2])
                except (OSError, ValueError, KeyError):
                    result[source] = empty
                    self._cache.pop(source, None)
            self._cache = {key: value for key, value in self._cache.items() if key in result}
        return result, evidence
