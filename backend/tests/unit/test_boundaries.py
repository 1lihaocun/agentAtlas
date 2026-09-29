from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading

import pytest

from atlas.assets.catalog import discover, read_text
from atlas.core.config import AppConfig
from atlas.core.storage import Conflict, Store, StorageProblem
from atlas.instructions.scanner import scan, main as scan_main
from atlas.instructions.effective import resolve_codex
from atlas.jobs.service import JobService
from atlas.files.service import FileProblem
from atlas.search.index import SearchIndex
from atlas.evaluation.runner import main as eval_main


@pytest.fixture
def root():
    with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[3] / '.agentatlas/work') as directory:
        yield Path(directory)


def put(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_discovery_real_platform_files_and_exclusions(root):
    project = root / 'Code/repository'
    rule = put(project / 'AGENTS.md', '# Project\nRules.\n')
    put(project / '.git/HEAD', 'ref: refs/heads/main\n')
    put(project / '.cursor/rules/work.mdc', '# Cursor instructions\n')
    memory = put(root / '.codex/memories/MEMORY.md', '# Memory\nRemember conventions.\n')
    skill = put(root / '.codex/skills/demo/SKILL.md', '# Skill\nRead source.\n')
    put(root / '.codex/auth.json', '{"access_token":"private"}')
    put(project / 'credentials/AGENTS.md', 'Excluded credential path')
    put(project / 'node_modules/dependency/AGENTS.md', 'Excluded dependency')
    put(project / '.agentatlas/AGENTS.md', 'Excluded state')
    alias = project / 'CLAUDE.md'
    alias.symlink_to(rule)
    instructions = scan([str(project)], home=root)
    assert str(alias) not in {entry['path'] for entry in instructions['files']}
    catalog = discover(home=root, instruction_files=instructions['files'], scan_roots=[str(project)])
    entries = {entry['path']: entry for entry in catalog['files']}
    assert entries[str(rule)]['editable']
    assert entries[str(memory)]['category'] == 'memory'
    assert entries[str(skill)]['category'] == 'skill'
    assert all(not any(value in str(Path(path).relative_to(root)) for value in ['credentials/', 'node_modules/', '.agentatlas/', 'auth.json']) for path in entries)
    assert read_text(entries[str(memory)])['content'].startswith('# Memory')


def test_scanner_publish_and_repeat(root):
    project = root / 'repo'
    path = put(project / 'AGENTS.md', '# Rules\n')
    config = AppConfig(workspace=root, home=root, scan_roots=[str(project)])
    scan_main(config)
    first = json.loads(config.instruction_path.read_text())
    assert first['totalFiles'] == 1
    path.unlink()
    scan_main(config)
    second = json.loads(config.instruction_path.read_text())
    assert second['files'] == [] and second['dupGroups'] == []


@pytest.mark.parametrize('budget', [0, 1, 4])
def test_effective_budget_returns_partial(root, budget):
    project = root / 'project'
    put(project / 'AGENTS.md', 'x' * 65536)
    (root / '.codex').mkdir()
    result = resolve_codex(project, project, root / '.codex', max_bytes=budget)
    assert result['sourceStatus'] == 'partial'
    assert result['files'] == [] and result['truncated']


def test_storage_concurrent_save_and_durable_backup(root):
    path = put(root / 'project/AGENTS.md', 'original')
    version = hashlib.sha256(path.read_bytes()).hexdigest()
    stores = [Store(root / 'state'), Store(root / 'state')]
    barrier = threading.Barrier(2)
    def write(index):
        barrier.wait()
        try:
            return stores[index].save(str(path), version, f'writer-{index}'.encode(), {str(path)}, 'test', 1000)
        except Conflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, range(2)))
    assert results.count('conflict') == 1
    saved = next(result for result in results if result != 'conflict')
    assert Path(saved['backup']).read_text() == 'original'
    assert len(stores[0].edits(str(path))) == 1
    assert stores[0].edits(str(path))[0]['status'] == 'committed'
    assert Store(root / 'state').edits(str(path)) == stores[0].edits(str(path))


def test_storage_private_schema_and_review_reopen(root):
    store = Store(root / 'state')
    assert store.state_dir.stat().st_mode & 0o777 == 0o700
    assert store.db_path.stat().st_mode & 0o777 == 0o600
    path = put(root / 'AGENTS.md', 'rules')
    store.decide(str(path), 'version', 'snooze', 1000, 30)
    assert Store(root / 'state').review(str(path), 'version')['until'] == 1000 + 30 * 86400
    assert store.review(str(path), 'different') is None
    with sqlite3.connect(store.db_path) as connection:
        connection.execute('PRAGMA user_version = 42')
    with pytest.raises(StorageProblem):
        Store(root / 'state')


def test_storage_rejects_symlinks_and_unlisted_files(root):
    path = put(root / 'private.md', 'private')
    alias = root / 'AGENTS.md'
    alias.symlink_to(path)
    store = Store(root / 'state')
    version = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(StorageProblem):
        store.save(str(alias), version, b'changed', {str(alias)}, 'test', 1000)
    with pytest.raises(StorageProblem):
        store.save(str(path), version, b'changed', set(), 'test', 1000)
    assert path.read_text() == 'private'


def test_keyword_index_update_delete_and_literal_input(root):
    path = put(root / 'MEMORY.md', '# Memory\n中文 Searchtoken phrase.\n')
    entry = dict(path=str(path), name=path.name, category='memory', platform='codex', searchable=True, editable=True)
    index = SearchIndex(str(root / 'search.sqlite3'))
    index.sync([entry], read_text)
    assert index.search('Searchtoken', mode='keyword')['total'] == 1
    assert index.search('中文', mode='keyword')['total'] == 1
    assert index.search('OR *', mode='keyword')['total'] == 0
    path.write_text('# Changed\nDifferent phrase.\n')
    index.sync([entry], read_text)
    assert index.search('Searchtoken', mode='keyword')['total'] == 0
    index.sync([], read_text)
    assert index.search('Different', mode='keyword')['total'] == 0


def test_jobs_reject_overlap_and_complete_queued_refresh():
    jobs = JobService()
    started, release, refreshed = threading.Event(), threading.Event(), threading.Event()
    def operation():
        started.set()
        assert release.wait(5)
        return {'finished': True}
    try:
        jobs.start('scan', operation)
        assert started.wait(5)
        with pytest.raises(FileProblem):
            jobs.start('index', operation)
        jobs.refresh(lambda: refreshed.set())
        assert jobs.status()['refreshQueued']
        release.set()
        assert refreshed.wait(5)
    finally:
        release.set()
        jobs.close()
    assert jobs.status()['stage'] == 'done'


def test_jobs_failure_is_visible():
    jobs = JobService()
    def fail():
        raise ValueError('输入参数错误')
    jobs.start('failure', fail)
    jobs.close()
    assert jobs.status()['stage'] == 'error'
    assert jobs.status()['error'] == '输入参数错误'


def test_evaluation_execution_requires_explicit_flag(root):
    with pytest.raises(SystemExit) as raised:
        eval_main(['pilot', 'missing.jsonl', '--output', str(root / 'output'), '--model', 'unconfigured', '--max-calls', '1'])
    assert raised.value.code == 2
    assert not (root / 'output').exists()
