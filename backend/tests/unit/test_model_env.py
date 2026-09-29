import json

import pytest

from atlas.core.model_env import ModelEnvironment
from atlas.files.service import FileProblem
from atlas.settings.service import SettingsStore
from atlas.translation.service import TranslationService


def test_embedding_reads_workspace_dotenv_without_writing_and_reloads(tmp_path):
    path = tmp_path / '.env'
    path.write_text(
        '# Keep translation settings\n'
        'ATLAS_TRANSLATION_MODEL=translation-fixture\n'
        'ATLAS_EMBEDDING_BASE_URL=https://embed.example/v1\n'
        'ATLAS_EMBEDDING_MODEL=embedding-fixture\n'
        'ATLAS_EMBEDDING_API_KEY=fixture-secret\n'
        'ATLAS_EMBEDDING_DIMENSIONS=1024\n'
        'ATLAS_EMBEDDING_BATCH_SIZE=8\n'
        'ATLAS_EMBEDDING_CATEGORIES=memory,skill\n', encoding='utf-8')
    store = SettingsStore(tmp_path)
    assert store.public() == {
        'baseUrl': 'https://embed.example/v1', 'model': 'embedding-fixture',
        'dimensions': 1024, 'batchSize': 8, 'categories': ['memory', 'skill'],
        'apiKeyConfigured': True,
    }
    assert 'fixture-secret' not in json.dumps(store.public())
    original = path.read_bytes()
    revision = path.stat()
    store.private()
    store.authorized()['_authorize']()
    assert path.read_bytes() == original
    assert path.stat() == revision
    path.write_bytes(original.replace(b'embedding-fixture', b'updated-fixture'))
    assert SettingsStore(tmp_path).private()['apiKey'] == 'fixture-secret'
    assert store.public()['model'] == 'updated-fixture'
    content = path.read_text(encoding='utf-8')
    assert '# Keep translation settings\nATLAS_TRANSLATION_MODEL=translation-fixture\n' in content

    assert not (tmp_path / 'settings.json').exists()
    assert not (tmp_path / 'embedding-key').exists()


def test_translation_uses_only_workspace_dotenv_and_reloads(tmp_path, monkeypatch):
    monkeypatch.setenv('ATLAS_TRANSLATION_MODEL', 'must-not-inherit')
    monkeypatch.setenv('ATLAS_TRANSLATION_API_KEY', 'must-not-inherit')
    path = tmp_path / '.env'
    path.write_text(
        'ATLAS_TRANSLATION_PROVIDER=ollama\nATLAS_TRANSLATION_MODEL=local-fixture\n'
        'ATLAS_TRANSLATION_BASE_URL=http://localhost:11434/v1/\n', encoding='utf-8')
    translator = TranslationService(None, tmp_path, tmp_path / '.agentatlas')
    assert translator.configuration() == {
        'provider': 'ollama', 'model': 'local-fixture',
        'base': 'http://localhost:11434/v1', 'key': '',
    }
    path.write_text(path.read_text(encoding='utf-8').replace('local-fixture', 'other-fixture'), encoding='utf-8')
    assert translator.configuration()['model'] == 'other-fixture'


def test_literal_credentials_and_duplicate_assignments_are_read_without_rewriting(tmp_path, monkeypatch):
    path = tmp_path / '.env'
    monkeypatch.setenv('DO_NOT_EXPAND', 'must-not-expand')
    content = ('export ATLAS_EMBEDDING_MODEL=first\nATLAS_EMBEDDING_MODEL=中文模型\n'
               r"ATLAS_EMBEDDING_API_KEY='fixture-\'quote\'\\slash # ${DO_NOT_EXPAND}'" '\n'
               'UNRELATED="two\nlines"')
    path.write_text(content, encoding='utf-8')
    store = SettingsStore(tmp_path)
    secret = "fixture-'quote'\\slash # ${DO_NOT_EXPAND}"
    assert store.private()['apiKey'] == secret
    assert store.public()['model'] == '中文模型'
    assert ModelEnvironment(tmp_path).read()['UNRELATED'] == 'two\nlines'
    assert path.read_text(encoding='utf-8') == content
    assert path.read_text(encoding='utf-8').count('ATLAS_EMBEDDING_MODEL=') == 2
    assert secret not in json.dumps(store.public())


def test_direct_dotenv_changes_revoke_embedding_authorization(tmp_path):
    store = SettingsStore(tmp_path)
    path = tmp_path / '.env'
    path.write_text("ATLAS_EMBEDDING_CATEGORIES='memory'\n", encoding='utf-8')
    approved = store.authorized()
    approved['_authorize']()
    original = path.read_text(encoding='utf-8')
    path.write_text(original.replace("'memory'", "'memory,session'"), encoding='utf-8')
    path.write_text(original, encoding='utf-8')
    with pytest.raises(ValueError, match='授权'):
        approved['_authorize']()
    assert approved['categories'] == ['memory']


def test_empty_categories_and_missing_dotenv_do_not_inherit_legacy_or_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('ATLAS_EMBEDDING_MODEL', 'must-not-inherit')
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-inherit')
    monkeypatch.setenv('ATLAS_TRANSLATION_MODEL', 'must-not-inherit')
    state = tmp_path / '.agentatlas'
    state.mkdir()
    (state / 'settings.json').write_text('{"model": "legacy"}', encoding='utf-8')
    (state / 'embedding-key').write_text('legacy-fixture', encoding='utf-8')
    hermes = tmp_path / '.hermes'
    hermes.mkdir()
    (hermes / 'config.yaml').write_text('model:\n  provider: ollama\n  default: legacy\n', encoding='utf-8')
    (hermes / '.env').write_text('ATLAS_TRANSLATION_MODEL=legacy\n', encoding='utf-8')
    store = SettingsStore(tmp_path)
    assert store.public()['model'] == ''
    assert not store.public()['apiKeyConfigured']
    translator = TranslationService(None, tmp_path, state)
    with pytest.raises(FileProblem, match=r'\.env'):
        translator.configuration()
    (tmp_path / '.env').write_text('ATLAS_EMBEDDING_CATEGORIES=\n', encoding='utf-8')
    assert store.public()['categories'] == []


def test_template_is_safe_without_model_credentials(tmp_path):
    from pathlib import Path

    template = Path(__file__).resolve().parents[3] / '.env.example'
    (tmp_path / '.env').write_bytes(template.read_bytes())
    store = SettingsStore(tmp_path)
    assert store.public()['batchSize'] == 16
    assert store.public()['dimensions'] is None
    assert not store.public()['apiKeyConfigured']
    with pytest.raises(FileProblem, match=r'\.env'):
        TranslationService(None, tmp_path, tmp_path / '.agentatlas').configuration()


def test_dotenv_revision_rejects_external_key_rotation(tmp_path):
    env = ModelEnvironment(tmp_path)
    env.path.write_text('ATLAS_EMBEDDING_API_KEY=old-fixture\n', encoding='utf-8')
    revision = env.revision()
    env.path.write_text('ATLAS_EMBEDDING_API_KEY=rotated-fixture\n', encoding='utf-8')
    with pytest.raises(ValueError, match='配置已变更'):
        env.check_revision(revision)
    assert env.read() == {'ATLAS_EMBEDDING_API_KEY': 'rotated-fixture'}


@pytest.mark.parametrize('change', ['create', 'remove', 'replace'])
def test_dotenv_revision_detects_file_lifecycle_changes(tmp_path, change):
    env = ModelEnvironment(tmp_path)
    content = 'ATLAS_EMBEDDING_MODEL=fixture\n'
    if change != 'create':
        env.path.write_text(content, encoding='utf-8')
    revision = env.revision()
    env.check_revision(revision)
    if change == 'create':
        env.path.write_text(content, encoding='utf-8')
    elif change == 'remove':
        env.path.unlink()
    else:
        replacement = tmp_path / '.env.replacement'
        replacement.write_text(content, encoding='utf-8')
        replacement.replace(env.path)
    with pytest.raises(ValueError, match='配置已变更'):
        env.check_revision(revision)
