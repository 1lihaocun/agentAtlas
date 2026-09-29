import json

import pytest

from atlas.settings.service import SettingsStore


def test_secrets_never_returned_and_external_clear_reloads(tmp_path):
    path = tmp_path / '.env'
    content = ('ATLAS_EMBEDDING_BASE_URL=https://embed.example/v1\n'
               'ATLAS_EMBEDDING_MODEL=test-model\nATLAS_EMBEDDING_API_KEY=fixture-secret\n')
    path.write_text(content, encoding='utf-8')
    store = SettingsStore(tmp_path)
    public = store.public()
    assert public['apiKeyConfigured']
    assert 'apiKey' not in public
    assert 'fixture-secret' not in json.dumps(public)
    assert store.private()['apiKey'] == 'fixture-secret'
    assert path.read_text(encoding='utf-8') == content
    assert not (tmp_path / 'settings.json').exists()
    assert not (tmp_path / 'embedding-key').exists()
    path.write_text(content.replace('test-model', 'second-model'), encoding='utf-8')
    assert store.private()['apiKey'] == 'fixture-secret'
    assert store.public()['model'] == 'second-model'
    path.write_text(content.replace('fixture-secret', ''), encoding='utf-8')
    assert not store.public()['apiKeyConfigured']
    assert store.private()['apiKey'] == ''


@pytest.mark.parametrize('assignment', [
    'BASE_URL=http://remote.example/v1',
    'BASE_URL=https://user:fixture-secret@remote.example/v1',
    'BASE_URL=https://remote.example/v1?key=fixture-secret',
    'BASE_URL=https://remote.example/v1#fragment',
    'BASE_URL=https://remote.example:invalid/v1',
    'BASE_URL=' + 'x' * 2049,
    'DIMENSIONS=-1', 'DIMENSIONS=True', 'DIMENSIONS=1.5', 'DIMENSIONS=65537',
    'BATCH_SIZE=0', 'BATCH_SIZE=129', 'BATCH_SIZE=wrong',
    'CATEGORIES=made-up', 'MODEL=' + 'x' * 2049,
    'API_KEY=' + 'x' * 8193, 'API_KEY="fixture\nsecret"', 'API_KEY="fixture\\rsecret"',
])
def test_invalid_configuration_is_rejected_without_rewriting(tmp_path, assignment):
    path = tmp_path / '.env'
    content = 'ATLAS_EMBEDDING_' + assignment + '\n'
    path.write_text(content, encoding='utf-8')
    store = SettingsStore(tmp_path)
    for operation in (store.public, store.private, store.authorized):
        with pytest.raises(ValueError, match=r'\.env.*修复') as caught:
            operation()
        assert 'fixture-secret' not in str(caught.value)
    assert path.read_text(encoding='utf-8') == content
    path.write_text('ATLAS_EMBEDDING_MODEL=repaired\n', encoding='utf-8')
    assert store.public()['model'] == 'repaired'


def test_cloud_scope_excludes_private_high_volume_categories_by_default(tmp_path):
    store = SettingsStore(tmp_path)
    data = store.public()
    assert not data['apiKeyConfigured']
    assert data['categories'] == ['instruction', 'memory', 'skill', 'reference', 'command', 'hook']
    assert not (tmp_path / '.env').exists()
    (tmp_path / '.env').write_text('ATLAS_EMBEDDING_DIMENSIONS=1024\n', encoding='utf-8')
    assert store.public()['dimensions'] == 1024
    (tmp_path / '.env').write_text('ATLAS_EMBEDDING_DIMENSIONS=\n', encoding='utf-8')
    assert store.public()['dimensions'] is None


def test_authorization_snapshot_is_revoked_by_changes_even_if_reverted(tmp_path):
    path = tmp_path / '.env'
    original = ('ATLAS_EMBEDDING_BASE_URL=https://embed.example/v1\n'
                'ATLAS_EMBEDDING_MODEL=fixture\nATLAS_EMBEDDING_API_KEY=fixture-secret\n'
                'ATLAS_EMBEDDING_CATEGORIES=memory\n')
    path.write_text(original, encoding='utf-8')
    store = SettingsStore(tmp_path)
    approved = store.authorized()
    approved['_authorize']()
    path.write_text(original.replace('CATEGORIES=memory', 'CATEGORIES=memory,session'), encoding='utf-8')
    path.write_text(original, encoding='utf-8')
    with pytest.raises(ValueError, match='授权'):
        approved['_authorize']()
    assert approved['categories'] == ['memory']
    assert '_authorize' not in store.public()


def test_authorization_detects_external_key_removal(tmp_path):
    path = tmp_path / '.env'
    path.write_text('ATLAS_EMBEDDING_API_KEY=fixture-secret\n', encoding='utf-8')
    store = SettingsStore(tmp_path)
    approved = store.authorized()
    path.write_text('ATLAS_EMBEDDING_API_KEY=\n', encoding='utf-8')
    assert not SettingsStore(tmp_path).public()['apiKeyConfigured']
    with pytest.raises(ValueError, match='授权'):
        approved['_authorize']()
