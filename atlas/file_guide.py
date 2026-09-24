"""File-type documentation, kept separate from authorization and memory scope."""
from copy import deepcopy
from datetime import datetime
from fnmatch import fnmatchcase
import json
from pathlib import Path, PurePosixPath
import re
import threading

REGISTRY = Path(__file__).resolve().parents[1] / 'docs' / 'file-types.json'
_ACTIVITY = re.compile(r'(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})-(?P<identifier>[A-Za-z]{4})-(?P<window>10min|6h)-(?P<slug>[^/]+)(?P<extension>\.md)')
_PROPOSAL = re.compile(r'(?P<identifier>[0-9a-f]{8})(?P<extension>\.json)')


class FileGuide:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else REGISTRY
        self._stamp = None
        self._data = None
        self._lock = threading.RLock()

    def _load(self):
        with self._lock:
            info = self.path.stat()
            stamp = (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            if stamp != self._stamp:
                with self.path.open('rb') as stream:
                    raw = stream.read(524289)
                if len(raw) > 524288:
                    raise ValueError('文件说明库超过 512 KiB')
                data = json.loads(raw)
                self._validate(data)
                self._data, self._stamp = data, stamp
            return self._data

    @staticmethod
    def _validate(data):
        if not isinstance(data, dict) or data.get('schemaVersion') != 1:
            raise ValueError('文件说明库版本无效')
        if not all(isinstance(data.get(key), str) for key in ('title', 'notice', 'reviewedAt')):
            raise ValueError('文件说明库缺少标题或复核日期')
        if not isinstance(data.get('sources'), dict) or not isinstance(data.get('filenameFormats'), dict) or not isinstance(data.get('types'), list):
            raise ValueError('文件说明库结构无效')
        for key, format_ in data['filenameFormats'].items():
            if key not in {'activity', 'skysight', 'fixed', 'opaque', 'proposal'} or not isinstance(format_, dict):
                raise ValueError('未支持的命名解析器')
            if not isinstance(format_.get('fields'), dict) or not isinstance(format_.get('cautions'), list):
                raise ValueError('命名字段定义无效')
            required = {'activity': {'timestamp', 'identifier', 'window', 'slug', 'extension'}, 'skysight': {'timestamp', 'identifier', 'window', 'slug', 'extension'}, 'fixed': {'name'}, 'opaque': set(), 'proposal': {'identifier', 'extension'}}[key]
            if set(format_['fields']) != required or any(not isinstance(value, dict) or not all(isinstance(value.get(k), str) for k in ('label', 'meaning')) for value in format_['fields'].values()):
                raise ValueError('命名字段说明不完整')
        allowed = {'id', 'label', 'role', 'match', 'purpose', 'producer', 'consumer', 'cautions', 'naming', 'evidence', 'sources', 'reviewedAt', 'generationMechanism', 'loadingMechanism'}
        ids = set()
        for kind in data['types']:
            if not isinstance(kind, dict) or set(kind) - allowed or not all(isinstance(kind.get(key), str) for key in ('id', 'label', 'role', 'purpose', 'producer', 'consumer', 'naming', 'evidence', 'reviewedAt')):
                raise ValueError('文件类型字段无效')
            if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]*', kind['id']) or kind['id'] in ids:
                raise ValueError('文件类型 ID 无效或重复')
            ids.add(kind['id'])
            if kind['naming'] not in data['filenameFormats'] or not isinstance(kind.get('sources'), list) or not kind['sources'] or any(key not in data['sources'] for key in kind['sources']):
                raise ValueError('类型引用的命名或来源不存在')
            match = kind.get('match')
            if not isinstance(match, dict) or not match or set(match) - {'platforms', 'categories', 'names', 'suffixes', 'homePaths'} or any(not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value) for value in match.values()):
                raise ValueError('文件类型匹配规则无效')
            if any(pattern.startswith('/') or any(part in {'', '.', '..', '**'} for part in pattern.split('/')) for pattern in match.get('homePaths', [])):
                raise ValueError('路径规则须相对于 HOME；通配符不跨目录')

    def library(self):
        data = self._load()
        return {'schemaVersion': 1, 'title': data['title'], 'notice': data['notice'],
                'reviewedAt': data['reviewedAt'], 'maintenancePath': 'docs/file-types.json',
                'types': [self._description(kind, data) for kind in data['types']]}

    @staticmethod
    def _description(kind, data, name=None):
        result = deepcopy({key: value for key, value in kind.items() if key not in {'match', 'naming', 'sources'}})
        result['notice'] = data['notice']
        result['matchPatterns'] = ['~/' + pattern for pattern in kind['match'].get('homePaths', [])] or kind['match'].get('names', []) or ['清单类别：' + ', '.join(kind['match'].get('categories', []))]
        result['sources'] = [deepcopy(data['sources'][key]) for key in kind['sources']]
        docs = [source['url'] for source in result['sources']
                if isinstance(source, dict) and source.get('kind') == '官方文档'
                and isinstance(source.get('url'), str) and source['url'].startswith(('http://', 'https://'))]
        if docs:
            result['docs'] = docs
        naming = deepcopy(data['filenameFormats'][kind['naming']])
        definitions = naming.pop('fields')
        values = {}
        if name is not None and kind['naming'] in {'activity', 'skysight'}:
            match = _ACTIVITY.fullmatch(name)
            if match and kind['naming'] == 'skysight' and match['slug'] != 'memory-summary':
                match = None
            if match:
                try:
                    datetime.strptime(match['timestamp'], '%Y-%m-%dT%H-%M-%S')
                    values = match.groupdict()
                except ValueError:
                    pass  # A date-shaped string may still be an impossible date.
        elif name is not None and kind['naming'] == 'fixed':
            values = {'name': name}
        elif name is not None and kind['naming'] == 'proposal':
            match = _PROPOSAL.fullmatch(name)
            if match:
                values = match.groupdict()
        naming.update(original=name, status='template' if name is None else 'matched' if values else 'unknown',
                      fields=[dict(key=key, value=value, **definitions[key]) for key, value in values.items()])
        if name is None:
            naming['fields'] = [dict(key=key, **value) for key, value in definitions.items()]
        result['filename'] = naming
        return result

    def describe(self, entry, home=None):
        data = self._load()
        source = str(entry.get('path', ''))
        path = PurePosixPath(source)
        unknown = {'id': 'unknown', 'label': '尚未收录的文件类型', 'role': '未知',
                   'purpose': '说明库暂未记录此路径的专门用途，不由文件名猜测读取规则。',
                   'filename': {'original': path.name, 'status': 'unknown', 'fields': [], 'cautions': []},
                   'sources': [], 'notice': data['notice']}
        if not path.is_absolute() or '..' in path.parts or str(path) != source or '\x00' in source:
            return unknown
        home = PurePosixPath(str(home if home is not None else Path.home()))
        try:
            relative = path.relative_to(home).parts
        except ValueError:
            relative = ()
        selected = None
        for kind in data['types']:
            match = kind['match']
            if any(match.get(key) and entry.get(field) not in match[key]
                   for key, field in [('platforms', 'platform'), ('categories', 'category')]):
                continue
            if match.get('names') and path.name not in match['names']:
                continue
            if match.get('suffixes') and path.suffix not in match['suffixes']:
                continue
            if match.get('homePaths') and not any(
                    len(relative) == len(pattern.split('/')) and
                    all(fnmatchcase(part, glob) for part, glob in zip(relative, pattern.split('/')))
                    for pattern in match['homePaths']):
                continue
            selected = kind
            break
        if selected is None:
            return unknown
        return self._description(selected, data, path.name)
