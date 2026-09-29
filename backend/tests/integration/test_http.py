import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

import httpx
import pytest

REPOSITORY = Path(__file__).resolve().parents[3]


def wait_ready(client):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            response = client.get('/api/assets/status')
        except httpx.ConnectError:
            time.sleep(.1)
            continue
        response.raise_for_status()
        status = response.json()['data']
        if not status['job']['running']:
            assert not status['job']['error'], status
            assert status['available'], status
            return status
        time.sleep(.1)
    raise AssertionError('扫描未在规定时间内完成')


def stop_process(process, timeout=15.0):
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def test_stop_process_kills_unresponsive_child(tmp_path):
    program = tmp_path / 'ignore_term.py'
    program.write_text('import signal\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nprint("ready", flush=True)\nsignal.pause()\n', encoding='utf-8')
    with subprocess.Popen([sys.executable, str(program)], stdout=subprocess.PIPE, text=True, encoding='utf-8') as process:
        try:
            assert process.stdout.readline().strip() == 'ready'
            stop_process(process, timeout=.1)
            assert process.poll() is not None
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


@pytest.fixture(scope='module')
def server():
    root = REPOSITORY / '.agentatlas/work' / ('http-' + uuid.uuid4().hex)
    home = root / 'home'
    project = home / 'Code/project'
    project.mkdir(parents=True)
    (project / '.git').mkdir()
    for index in range(64):
        folder = project / f'feature-{index:02}'
        folder.mkdir()
        (folder / 'AGENTS.md').write_text(f'# Feature {index}\nUnique searchneedle{index} 中文规则。\n## Notes\nKeep boundaries.\n', encoding='utf-8')
    (project / 'AGENTS.md').write_text('# Project\nKeep changes reviewable.\n', encoding='utf-8')
    (project / 'CLAUDE.md').write_text('# Project\nKeep changes reviewable.\n', encoding='utf-8')
    memory = home / '.codex/memories'
    (memory / 'extensions/custom/notes').mkdir(parents=True)
    (memory / 'MEMORY.md').write_text('---\ntitle: 项目记忆\ndescription: |\n  第一行说明\n  第二行说明\n---\n# Memory\nRead local evidence.\n', encoding='utf-8')
    (memory / 'extensions/custom/notes/day.md').write_text('# 日常记录\nSource project: ' + str(project) + '\n', encoding='utf-8')
    (home / '.codex/sessions').mkdir()
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    environment = dict(os.environ, ATLAS_HOME=str(home), ATLAS_SCAN_ROOTS=json.dumps([str(project)]),
                       TMPDIR=str(root), ATLAS_FRONTEND_DIR=str(REPOSITORY / 'frontend/dist'))
    with (root / 'server.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen([sys.executable, '-m', 'atlas', '--workspace', str(root), 'serve', '--port', str(port)],
                                   cwd=REPOSITORY, env=environment, stdout=log, stderr=log)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=15, trust_env=False) as client:
                wait_ready(client)
                yield {'client': client, 'root': root, 'home': home, 'project': project, 'memory': memory, 'port': port}
        finally:
            stop_process(process)


def data(server, endpoint, **params):
    response = server['client'].get('/api' + endpoint, params=params)
    assert response.status_code == 200, response.text
    return response.json()['data']


def test_catalog_pagination_and_filters(server):
    first = data(server, '/assets', page=1, pageSize=25, category='instruction')
    second = data(server, '/assets', page=2, pageSize=25, category='instruction')
    assert first['total'] == 66
    assert len(first['items']) == len(second['items']) == 25
    assert not set(row['path'] for row in first['items']) & set(row['path'] for row in second['items'])
    assert first['catalogVersion'] == second['catalogVersion']
    last = data(server, '/assets', page=999, pageSize=25, category='instruction')
    assert last['page'] == 3 and len(last['items']) == 16
    assert data(server, '/assets', platform='unknown')['total'] == 0


@pytest.mark.parametrize('params', [{'page': 0}, {'pageSize': 1000}, {'page': 'abc'}, {'unknown': 'x'}, {'sort': 'unknown'}])
def test_invalid_listing_parameters(server, params):
    assert server['client'].get('/api/assets', params=params).status_code == 422


def test_memory_metadata_directory_and_context(server):
    result = data(server, '/memories')
    assert result['total'] == 2
    root = str(server['memory'])
    node = result['storageTree']['nodes'][root]
    assert (node['directCount'], node['totalCount']) == (1, 2)
    direct = data(server, '/memories', path=root)
    assert direct['total'] == 1
    assert direct['items'][0]['memoryInfo']['title'] == '项目记忆'
    assert direct['items'][0]['memoryInfo']['description'] == '第一行说明 第二行说明'
    assert data(server, '/memories', path=root, recursive='true')['total'] == 2
    context = data(server, '/file/context', path=root + '/MEMORY.md')
    assert context['memory']['memoryStorage']['directory'] == root
    assert context['fileGuide']['id']
    assert server['client'].get('/api/memories', params={'path': root + '/missing'}).status_code == 404


def test_local_search_and_cloud_consent(server):
    result = data(server, '/search', q='searchneedle37')
    assert result['total'] >= 1
    assert any('feature-37' in row['path'] for row in result['items'])
    assert server['client'].get('/api/search', params={'q': 'test', 'mode': 'vector'}).status_code == 422
    response = server['client'].post('/api/search/execute', json={'q': 'test', 'mode': 'vector', 'confirmCloud': False})
    assert response.status_code == 400
    response = server['client'].post('/api/search/index', json={'embeddings': True, 'confirmCloud': False})
    assert response.status_code == 400
    response = server['client'].get('/api/search/results/' + '0' * 32)
    assert response.status_code == 404


def test_save_version_conflict_backups_and_sections(server):
    path = server['project'] / 'feature-00/AGENTS.md'
    original = path.read_bytes()
    read = data(server, '/file', path=str(path))
    changed = '# New header\n更新内容。\n## Details\nRetained tail.\n'
    result = server['client'].post('/api/save', json={'path': str(path), 'content': changed, 'baseVersion': read['version']})
    assert result.status_code == 200, result.text
    assert path.read_text(encoding='utf-8') == changed
    saved = result.json()['data']
    assert Path(saved['backup']).read_bytes() == original
    conflict = server['client'].post('/api/save', json={'path': str(path), 'content': 'stale', 'baseVersion': read['version']})
    assert conflict.status_code == 409
    assert path.read_text(encoding='utf-8') == changed
    sections = data(server, '/sections', path=str(path))
    first = sections['sections'][0]
    result = server['client'].post('/api/section/save', json={'path': str(path), 'sectionId': first['id'],
        'baseVersion': sections['version'], 'text': '# New header\nSelected section.\n'})
    assert result.status_code == 200, result.text
    assert path.read_text(encoding='utf-8') == '# New header\nSelected section.\n## Details\nRetained tail.\n'
    wait_ready(server['client'])


def test_unlisted_and_symlink_files_rejected(server):
    private = server['home'] / 'private.md'
    private.write_text('outside catalog', encoding='utf-8')
    assert server['client'].get('/api/file', params={'path': str(private)}).status_code == 403
    target = server['project'] / 'feature-01/AGENTS.md'
    original = target.read_bytes()
    target.unlink()
    target.symlink_to(private)
    try:
        assert server['client'].get('/api/file', params={'path': str(target)}).status_code == 403
    finally:
        target.unlink()
        target.write_bytes(original)


def test_reviews_and_decisions_persist(server):
    result = data(server, '/retirement', bucket='recent')
    assert 'counts' in result
    path = str(server['project'] / 'AGENTS.md')
    read = data(server, '/file', path=path)
    response = server['client'].post('/api/retirement/decision', json={'path': path, 'baseVersion': read['version'], 'action': 'keep'})
    assert response.status_code == 200, response.text
    assert (server['root'] / '.agentatlas/lifecycle/state.sqlite3').is_file()
    evidence = data(server, '/usage/evidence', path=path)
    assert evidence['path'] == path and 'sourceStatus' in evidence


def test_duplicate_sync_checks_target_version(server):
    groups = data(server, '/duplicates')['items']
    assert any(len(group['files']) == 2 for group in groups)
    source = server['project'] / 'AGENTS.md'
    target = server['project'] / 'CLAUDE.md'
    read = data(server, '/file', path=str(source))
    target.write_text('external edit', encoding='utf-8')
    result = server['client'].post('/api/sync-dups', json={'source': str(source), 'baseVersion': read['version']})
    assert result.status_code == 200, result.text
    assert result.json()['data']['failed'] == 1
    assert target.read_text(encoding='utf-8') == 'external edit'


def test_settings_are_read_only_and_keep_api_key_private(server):
    env_file = server['root'] / '.env'
    original = env_file.read_bytes() if env_file.exists() else None
    content = (b'# Manually maintained fixture\nATLAS_TRANSLATION_MODEL=translation-fixture\n'
               b'ATLAS_EMBEDDING_MODEL=embedding-fixture\nATLAS_EMBEDDING_API_KEY=integration-secret\n')
    try:
        env_file.write_bytes(content)
        settings = data(server, '/settings')
        assert settings == {
            'baseUrl': '', 'model': 'embedding-fixture', 'dimensions': None, 'batchSize': 16,
            'categories': ['instruction', 'memory', 'skill', 'reference', 'command', 'hook'],
            'apiKeyConfigured': True,
        }
        assert 'integration-secret' not in json.dumps(settings)
        revision = env_file.stat()
        payload = {key: value for key, value in settings.items() if key != 'apiKeyConfigured'}
        payload.update(model='must-not-save', apiKey='must-not-save')
        response = server['client'].post('/api/settings', json=payload)
        assert response.status_code in (404, 405), response.text
        assert env_file.read_bytes() == content
        assert env_file.stat() == revision
        assert data(server, '/settings') == settings
        schema = server['client'].get('/api/openapi.json')
        assert schema.status_code == 200
        assert set(schema.json()['paths']['/api/settings']) == {'get'}
        assert 'SettingsRequest' not in schema.json()['components']['schemas']
        assert not (server['root'] / '.agentatlas/embedding-key').exists()
        assert not (server['root'] / '.agentatlas/settings.json').exists()
    finally:
        if original is None:
            env_file.unlink()
        else:
            env_file.write_bytes(original)


def test_settings_reload_workspace_env_without_exposing_file(server):
    env_file = server['root'] / '.env'
    original = env_file.read_text(encoding='utf-8') if env_file.exists() else None
    try:
        env_file.write_text('ATLAS_EMBEDDING_MODEL=changed-on-disk\nATLAS_EMBEDDING_API_KEY=reload-fixture-secret\n', encoding='utf-8')
        assert data(server, '/settings')['model'] == 'changed-on-disk'
        assert server['client'].get('/api/file', params={'path': str(env_file)}).status_code == 403
        assert 'reload-fixture-secret' not in server['client'].get('/.env').text
    finally:
        if original is None:
            env_file.unlink()
        else:
            env_file.write_text(original, encoding='utf-8')


@pytest.mark.parametrize('headers', [{'host': 'evil.example'}, {'origin': 'https://evil.example'}, {'sec-fetch-site': 'cross-site'}, {'host': '[invalid'}])
def test_local_origin_boundary(server, headers):
    assert server['client'].get('/api/health', headers=headers).status_code == 403


@pytest.mark.parametrize('content_type', ['Application/JSON', 'application/json ; charset=utf-8'])
def test_json_media_type_normalization(server, content_type):
    wait_ready(server['client'])
    response = server['client'].post('/api/rescan', content='{}', headers={'content-type': content_type})
    assert response.status_code == 202, response.text
    wait_ready(server['client'])


def test_bodyless_rescan(server):
    wait_ready(server['client'])
    response = server['client'].post('/api/rescan')
    assert response.status_code == 202, response.text
    wait_ready(server['client'])


def test_json_validation_and_static_routes(server):
    client = server['client']
    assert client.post('/api/rescan', content='{}').status_code == 415
    assert client.post('/api/rescan', content='{', headers={'content-type': 'application/json'}).status_code == 422
    assert client.get('/api/assets?page=1&page=2').status_code == 400
    assert client.get('/api/nonexistent').status_code == 404
    for route in ['/assets', '/memories/directory', '/instructions/directory', '/files', '/settings', '/file-types/codex.memory']:
        response = client.get(route)
        assert response.status_code == 200, response.text
        assert '<div id="root">' in response.text
    assert client.get('/assets/missing.js').status_code == 404
    assert data(server, '/file-guide')['types']


def test_rescan_detects_added_file(server):
    path = server['project'] / 'feature-new/AGENTS.md'
    path.parent.mkdir()
    path.write_text('# New file\nNewly added instruction.\n', encoding='utf-8')
    previous = data(server, '/assets')['catalogVersion']
    response = server['client'].post('/api/rescan', json={})
    assert response.status_code == 202, response.text
    wait_ready(server['client'])
    result = data(server, '/assets', q='feature-new')
    assert result['catalogVersion'] != previous
    assert len(result['items']) == 1
    assert result['items'][0]['path'] == str(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == data(server, '/file', path=str(path))['version']


def test_occupied_port_does_not_initialize_data(server):
    workspace = server['root'] / 'occupied-port'
    result = subprocess.run([sys.executable, '-m', 'atlas', '--workspace', str(workspace), 'serve', '--port', str(server['port'])],
                            cwd=REPOSITORY, capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode != 0
    assert not (workspace / '.agentatlas').exists()


def test_development_proxy_and_process_cleanup(server):
    with socket.socket() as backend_listener, socket.socket() as frontend_listener:
        backend_listener.bind(('127.0.0.1', 0))
        frontend_listener.bind(('127.0.0.1', 0))
        backend_port = backend_listener.getsockname()[1]
        frontend_port = frontend_listener.getsockname()[1]
    workspace = server['root'] / 'dev'
    workspace.mkdir()
    environment = dict(os.environ, ATLAS_HOME=str(server['home']), ATLAS_SCAN_ROOTS=json.dumps([str(server['project'])]),
                       ATLAS_WORKSPACE=str(workspace), ATLAS_PORT=str(backend_port), ATLAS_FRONTEND_PORT=str(frontend_port))
    origin = f'http://127.0.0.1:{frontend_port}'
    with (workspace / 'dev.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen(['node', 'scripts/dev.mjs'], cwd=REPOSITORY, env=environment, stdout=log, stderr=log)
        try:
            with httpx.Client(base_url=origin, trust_env=False, timeout=10) as client:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    assert process.poll() is None
                    try:
                        response = client.get('/api/health', headers={'origin': origin, 'sec-fetch-site': 'same-origin'})
                    except httpx.ConnectError:
                        time.sleep(.1)
                        continue
                    if response.status_code == 200:
                        break
                    time.sleep(.1)
                else:
                    raise AssertionError('开发代理未成功启动')
                assert response.json()['ok']
                assert '<div id="root">' in client.get('/memories/directory').text
                assert client.get('/api/health', headers={'origin': 'https://example.com'}).status_code == 403
        finally:
            stop_process(process, timeout=20)
    for port in (backend_port, frontend_port):
        with socket.socket() as listener:
            assert listener.connect_ex(('127.0.0.1', port)) != 0
