from functools import partial
import hashlib
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import threading
from types import SimpleNamespace
import warnings

import pytest

from atlas.files.service import FileProblem, FileStore
from atlas.translation.service import TranslationService


class PostedFileHandler(SimpleHTTPRequestHandler):
    # 将 POST 指向磁盘文件，用真实 HTTP 验证无效响应的解析边界。
    do_POST = SimpleHTTPRequestHandler.do_GET


@pytest.mark.parametrize('payload', ['not-json', '[]', '{"choices": []}', '{"choices": [{"message": {"content": 3}}]}'])
def test_translation_malformed_http_content_is_known_problem(tmp_path, payload):
    resource = tmp_path / 'v1/chat/completions'
    resource.parent.mkdir(parents=True)
    resource.write_text(payload, encoding='utf-8')
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(PostedFileHandler, directory=str(tmp_path)))
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        translator, request = service(tmp_path, base=f'http://127.0.0.1:{server.server_port}/v1')
        with pytest.raises(FileProblem, match='响应格式') as caught:
            translator.translate(request)
        assert caught.value.status == 502
        assert not translator.directory.exists()
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def test_translation_connection_failure_is_known_problem(tmp_path):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        translator, request = service(tmp_path, base=f'http://127.0.0.1:{port}/v1')
        with pytest.raises(FileProblem, match='连接') as caught:
            translator.translate(request)
        assert caught.value.status == 502

@pytest.mark.parametrize('content', ['{', '[]', '{}'])
def test_corrupt_translation_cache_fails_without_overwrite(tmp_path, content):
    translator, request = service(tmp_path)
    config = translator.configuration()
    fingerprint = hashlib.sha256(json.dumps([request.baseVersion, request.lang,
                                             config['base'], config['model']]).encode()).hexdigest()
    translator.directory.mkdir()
    cache = translator.directory / (fingerprint + '.json')
    cache.write_text(content, encoding='utf-8')
    with pytest.raises(FileProblem, match='翻译缓存.*修复'):
        translator.translate(request)
    assert cache.read_text(encoding='utf-8') == content


def test_translation_cache_uses_explicit_utf8(tmp_path):
    translator, request = service(tmp_path, '# 中文配置\nATLAS_TRANSLATION_PROVIDER=ollama\nATLAS_TRANSLATION_MODEL=local\n')
    config = translator.configuration()
    fingerprint = hashlib.sha256(json.dumps([request.baseVersion, request.lang,
                                             config['base'], config['model']]).encode()).hexdigest()
    translator.directory.mkdir()
    cache = translator.directory / (fingerprint + '.json')
    cached = {'path': request.path, 'lang': request.lang, 'translated': '缓存的中文',
              'source': '# 中文规则\n保持结构。\n', 'model': config['model'], 'provider': config['provider']}
    cache.write_text(json.dumps(cached, ensure_ascii=False), encoding='utf-8')
    with warnings.catch_warnings():
        warnings.simplefilter('error', EncodingWarning)
        assert translator.translate(request) == cached



def service(tmp_path, config='ATLAS_TRANSLATION_PROVIDER=ollama\nATLAS_TRANSLATION_MODEL=local\n',
            base='http://127.0.0.1:11434/v1'):
    home = tmp_path / 'home'
    home.mkdir()
    (tmp_path / '.agentatlas').mkdir()
    (tmp_path / '.env').write_text(config + 'ATLAS_TRANSLATION_BASE_URL=' + base + '\n', encoding='utf-8')
    path = home / 'AGENTS.md'
    path.write_text('# 中文规则\n保持结构。\n', encoding='utf-8')
    files = FileStore(lambda: [{'path': str(path), 'editable': True, 'category': 'instruction'}], tmp_path / 'backups')
    request = SimpleNamespace(path=str(path), confirmCloud=True, lang='English', baseVersion=files.read(str(path))['sha256'])
    return TranslationService(files, tmp_path, tmp_path / '.agentatlas'), request


@pytest.mark.parametrize('config', ['ATLAS_TRANSLATION_MODEL="broken\n', 'ATLAS_TRANSLATION_PROVIDER\n'])
def test_invalid_translation_configuration_explains_repair(tmp_path, config):
    translator, _ = service(tmp_path, config)
    with pytest.raises(FileProblem, match=r'\.env.*修复'):
        translator.configuration()


def test_invalid_translation_url_is_known_problem(tmp_path):
    translator, _ = service(tmp_path, base='http://[::1')
    with pytest.raises(FileProblem, match='地址'):
        translator.configuration()


def test_translation_stops_between_chunks_after_dotenv_edit(tmp_path):
    requests = []

    class ProtocolFixtureHandler(BaseHTTPRequestHandler):
        # 固定协议样例，仅验证授权边界，不代表真实模型推理结果。
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            if len(requests) == 1:
                (tmp_path / '.env').write_text('ATLAS_TRANSLATION_MODEL=changed\n', encoding='utf-8')
            payload = b'{"choices":[{"message":{"content":"protocol-fixture"}}]}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(('127.0.0.1', 0), ProtocolFixtureHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        translator, request = service(tmp_path, base=f'http://127.0.0.1:{server.server_port}/v1')
        Path(request.path).write_text('line\n' * 1300, encoding='utf-8')
        request.baseVersion = translator.files.read(request.path)['sha256']
        with pytest.raises(FileProblem, match='配置已变更') as caught:
            translator.translate(request)
        assert caught.value.status == 409
        assert len(requests) == 1
        assert not translator.directory.exists()
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def test_translation_http_failure_is_safe_known_problem(tmp_path):
    # 标准库静态服务真实拒绝 POST，不模拟模型成功响应。
    server = ThreadingHTTPServer(('127.0.0.1', 0), SimpleHTTPRequestHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        translator, request = service(tmp_path, base=f'http://127.0.0.1:{server.server_port}/v1')
        with pytest.raises(FileProblem, match='501') as caught:
            translator.translate(request)
        assert caught.value.status == 502
        assert not translator.directory.exists()
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
