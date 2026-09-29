import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import tracemalloc
from types import SimpleNamespace

import pytest

from atlas.app.dependencies import Services
from atlas.assets.schemas import AssetQuery
from atlas.assets.service import AssetService
from atlas.core.config import AppConfig
from atlas.duplicates.service import DuplicateService
from atlas.files.router import open_editor
from atlas.files.service import FileStore, FileProblem, MAX_FILE_BYTES
from atlas.http.schemas import FilePath
from atlas.jobs.service import JobService
from atlas.search.index import SearchIndex
from atlas.settings.service import SettingsStore


def test_queued_refresh_clears_previous_failure():
    jobs = JobService()
    release = threading.Event()
    states = []

    def fail():
        assert release.wait(5)
        raise ValueError('first operation failed')

    def refresh():
        states.append(jobs.status())
        return 'refreshed'

    try:
        jobs.start('index', fail)
        jobs.refresh(refresh)
        release.set()
    finally:
        release.set()
        jobs.close()
    assert states[0]['stage'] == 'queued'
    assert states[0]['error'] is None
    status = jobs.status()
    assert status['stage'] == 'done'
    assert status['error'] is None
    assert status['result'] == 'refreshed'
    assert status['kind'] == 'refresh'

def test_asset_listing_copies_only_selected_page(tmp_path):
    config = AppConfig(workspace=tmp_path, home=tmp_path)
    config.state_dir.mkdir()
    catalog = {'files': [{'path': f'/file-{number:04}.md', 'platform': 'shared',
                          'category': 'instruction', 'metadata': list(range(1000))}
                         for number in range(300)], 'counts': {'categories': {'instruction': 300}}}
    (config.state_dir / 'assets.json').write_text(json.dumps(catalog), encoding='utf-8')
    assets = AssetService(config, lambda: None)
    query = AssetQuery(q='file-0000')
    tracemalloc.start()
    try:
        result = assets.listing(query)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 200_000
    assert result['total'] == 1
    result['items'][0]['metadata'][0] = -1
    result['counts']['categories']['instruction'] = -1
    again = assets.listing(query)
    assert again['items'][0]['metadata'][0] == 0
    assert again['counts']['categories']['instruction'] == 300

def test_empty_codex_home_does_not_select_cwd(tmp_path):
    before = os.environ.get('CODEX_HOME')
    os.environ['CODEX_HOME'] = ''
    ctx = None
    try:
        ctx = Services(AppConfig(workspace=tmp_path, home=Path.home()))
        assert Path(ctx.provenance.codex_home) == Path.home() / '.codex'
    finally:
        if ctx is not None:
            ctx.jobs.close()
        if before is None:
            os.environ.pop('CODEX_HOME')
        else:
            os.environ['CODEX_HOME'] = before


def test_missing_editor_command_is_actionable(tmp_path):
    path = tmp_path / 'AGENTS.md'
    path.write_text('# test', encoding='utf-8')
    files = FileStore(lambda: [{'path': str(path), 'editable': True}], tmp_path / 'backups')
    before = os.environ.get('PATH')
    os.environ['PATH'] = str(tmp_path / 'empty-bin')
    try:
        with pytest.raises(FileProblem, match='编辑器.*安装') as caught:
            open_editor(FilePath(path=str(path)), SimpleNamespace(files=files))
        assert caught.value.status == 503
    finally:
        if before is None:
            os.environ.pop('PATH')
        else:
            os.environ['PATH'] = before



@pytest.mark.parametrize('raw', [b'# rule\n\xff', b'x' * (MAX_FILE_BYTES + 1)], ids=['invalid-utf8', 'oversized'])
@pytest.mark.parametrize('bad_source', [True, False])
def test_duplicate_sync_explains_nonversioned_files(tmp_path, raw, bad_source):
    source = tmp_path / 'AGENTS.md'
    target = tmp_path / 'CLAUDE.md'
    target.write_bytes(raw)
    source.write_bytes(raw if bad_source else b'# edited after scan\n')
    entries = [{'path': str(path), 'editable': True, 'category': 'instruction',
                'sha': hashlib.sha1(raw).hexdigest()} for path in (source, target)]
    files = FileStore(lambda: entries, tmp_path / 'backups')
    service = DuplicateService(lambda: {'files': entries}, files)
    if bad_source:
        with pytest.raises(FileProblem) as caught:
            service.sync(str(source), '0' * 64)
        assert caught.value.status == 415
        assert 'UTF-8' in str(caught.value) or '2 MiB' in str(caught.value)
    else:
        result = service.sync(str(source), files.read(str(source))['sha256'])
        assert result['results'][0]['status'] == 415
        assert result['failed'] == 1
    assert target.read_bytes() == raw



@pytest.mark.parametrize('content', ['ATLAS_EMBEDDING_MODEL="unterminated', 'ATLAS_EMBEDDING_BATCH_SIZE=wrong',
                                     'ATLAS_EMBEDDING_CATEGORIES=unknown', 'ATLAS_EMBEDDING_MODEL'])
def test_invalid_settings_fail_with_actionable_path(tmp_path, content):
    path = tmp_path / '.env'
    path.write_text(content, encoding='utf-8')
    store = SettingsStore(tmp_path)
    for operation in (store.public, store.private, store.authorized):
        with pytest.raises(ValueError, match=r'\.env.*修复'):
            operation()
    assert path.read_text(encoding='utf-8') == content


@pytest.mark.parametrize('space', [' ', '\u3000', '\u200b'])
def test_settings_reject_interior_url_whitespace(tmp_path, space):
    (tmp_path / '.env').write_text('ATLAS_EMBEDDING_BASE_URL=https://example.com/a' + space + 'b\n', encoding='utf-8')
    with pytest.raises(ValueError):
        SettingsStore(tmp_path).public()

@pytest.mark.parametrize('content', ['{', '[]', '{}', '{"files": [null], "counts": {}}'])
def test_invalid_asset_catalog_fails_with_actionable_path(tmp_path, content):
    config = AppConfig(workspace=tmp_path, home=tmp_path)
    config.state_dir.mkdir()
    path = config.state_dir / 'assets.json'
    path.write_text(content, encoding='utf-8')
    with pytest.raises(ValueError, match='assets.json.*修复'):
        AssetService(config, lambda: None)
    assert path.read_text(encoding='utf-8') == content


def test_asset_files_are_independent(tmp_path):
    config = AppConfig(workspace=tmp_path, home=tmp_path)
    config.state_dir.mkdir()
    catalog = {'files': [{'path': '/AGENTS.md', 'platform': 'shared',
                          'category': 'instruction', 'nested': {'a': 1}}], 'counts': {}}
    (config.state_dir / 'assets.json').write_text(json.dumps(catalog), encoding='utf-8')
    assets = AssetService(config, lambda: None)
    assets.files()[0]['nested']['a'] = 2
    assert assets.snapshot()['files'][0]['nested']['a'] == 1

def test_module_eval_propagates_failure(tmp_path):
    result = subprocess.run([sys.executable, '-m', 'atlas', 'eval', 'validate',
                             str(tmp_path / 'missing.jsonl')],
                            capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 2
    assert '"ok": false' in result.stderr


class SnippetParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, attrs))

    def handle_data(self, data):
        self.text.append(data)


@pytest.mark.parametrize('query', ['needle', '<img', '&', 'quot', 'AGENTS'])
def test_search_snippets_escape_source_markup(tmp_path, query):
    path = tmp_path / 'AGENTS.md'
    text = 'needle <img src=x onerror="alert(1)"> & "quoted" <mark>source</mark>\n'
    path.write_text(text, encoding='utf-8')
    index = SearchIndex(tmp_path / 'search.sqlite3')

    def read(entry):
        raw = path.read_bytes()
        return {'content': raw.decode('utf-8'), 'sha256': hashlib.sha256(raw).hexdigest()}

    index.sync([{'path': str(path), 'name': path.name}], read)
    result = index.search(query)
    assert result['total'] == 1
    parser = SnippetParser()
    parser.feed(result['results'][0]['snippet'])
    assert all(tag == 'mark' and attrs == [] for tag, attrs in parser.tags)
    assert ''.join(parser.text) == text.rstrip('\n')
