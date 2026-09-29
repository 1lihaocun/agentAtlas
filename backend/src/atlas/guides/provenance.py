from datetime import datetime, timezone
import json
import os
import re
import sqlite3
import time

from atlas.guides.service import FileGuide

MAX_SESSION_SCAN_BYTES = 64 * 1024 * 1024
MAX_SESSION_SCAN_SECONDS = 5
MAX_SCAN_FILES = 400
MAX_SESSION_LINE_BYTES = 2 * 1024 * 1024
MAX_TOOL_INPUT_CHARS = 4000
MAX_TOOL_OUTPUT_CHARS = 2000
SCAN_WINDOW_DAYS = 21
NOTE_NAME = re.compile(r'^(?P<stamp>\d{8}T\d{6}(?:Z|[+-]\d{4})?)-(?P<slug>.+)\.md$')


_MECHANISM_LABELS = {
    'codex.ad-hoc.note': '模型在会话中主动写入',
    'codex.rollout.summary': '后台 stage1 任务生成',
    'codex.memory.summary': '后台 consolidate 任务生成',
    'codex.memory.index': '后台 consolidate 任务生成',
    'codex.memory.raw': '后台任务生成',
    'codex.skysight.resource': '后台常驻进程生成',
    'codex.chronicle.resource': '后台常驻进程生成',
    'codex.extension.instructions': '随扩展安装落盘',
    'codex.memory.skill': '后台整理任务生成',
    'hermes.memory': '模型在会话中主动写入',
    'hermes.user': '模型在会话中主动写入',
    'hermes.memory.proposal': '模型在会话中主动写入',
}


def _mechanism(kind_id):
    label = _MECHANISM_LABELS.get(kind_id)
    return label if label else None


def _clamp(text, limit):
    if not isinstance(text, str):
        return ''
    return text if len(text) <= limit else text[:limit] + '…（截断）'


class SessionScanBudget:
    def __init__(self, max_bytes=MAX_SESSION_SCAN_BYTES, seconds=MAX_SESSION_SCAN_SECONDS):
        self.remaining = max_bytes
        self.deadline = time.monotonic() + seconds

    def check(self):
        if time.monotonic() >= self.deadline:
            raise ValueError('会话来源扫描超过时间预算，未完成取证')

    def read_line(self, stream):
        self.check()
        raw = stream.readline(min(MAX_SESSION_LINE_BYTES + 1, self.remaining + 1))
        self.remaining -= len(raw)
        if self.remaining < 0:
            raise ValueError('会话来源扫描超过字节预算，未完成取证')
        return raw


class ProvenanceService:
    """Answers “who wrote this file and under which rules” for catalog entries."""

    def __init__(self, guide=None, codex_home=None, home=None):
        self.guide = guide if guide is not None else FileGuide()
        self.home = home  # 清单 HOME；未指定时由说明库使用系统 HOME
        self.codex_home = os.path.realpath(os.path.expanduser(
            codex_home if codex_home is not None
            else os.environ.get('CODEX_HOME', os.path.join(os.path.expanduser('~'), '.codex'))))


    # ---- public API ------------------------------------------------------

    def describe(self, entry):
        path = str(entry.get('path', ''))
        guide = self.guide.describe(entry, home=self.home)
        result = {
            'path': path,
            'typeId': guide.get('id'),
            'label': guide.get('label'),
            'mechanism': _mechanism(guide.get('id')),
            'docs': self._docs(guide.get('id')),
            'reviewedAt': guide.get('reviewedAt'),
        }
        kind_id = guide.get('id')
        try:
            if kind_id in ('codex.memory.summary', 'codex.memory.index', 'codex.memory.raw',
                           'codex.rollout.summary'):
                result.update(self._codex_job_evidence(path))
            elif kind_id == 'codex.ad-hoc.note':
                result.update(self._adhoc_trace(path, entry))
            elif kind_id == 'codex.memory.skill':
                result.update(self._memory_skill_evidence())
            elif kind_id in ('codex.skysight.resource', 'codex.chronicle.resource'):
                result.update(self._extension_resource_evidence(kind_id))
            elif kind_id == 'hermes.memory.proposal':
                result.update({'evidence': [{
                    'kind': '机制说明', 'label': 'Hermes write_approval 提案目录',
                    'detail': '开启 write_approval 后，memory 工具写入先落为 pending/memory/ 下的提案 JSON，等用户在界面批准后才合并进 MEMORY.md / USER.md。'}],
                    'traced': False, 'note': '提案文件本身不含生成会话引用。'})
            elif kind_id in ('hermes.memory', 'hermes.user'):
                result.update(self._hermes_note(kind_id))
            else:
                result.update({'traced': False,
                               'note': '该类型暂无已核实的生成机制说明。'})
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            result['error'] = '生成机制取证失败：%s：%s' % (type(error).__name__, error)
        return result

    # ---- official doc links ---------------------------------------------

    def _docs(self, kind_id):
        if kind_id and kind_id.startswith('codex.'):
            return [{'label': 'OpenAI Codex 官方文档：Memories（本地记忆的生成与存储）',
                     'url': 'https://developers.openai.com/codex/customization/memories'}]
        if kind_id and kind_id.startswith('hermes.'):
            return [{'label': 'Hermes Agent 官方文档：Persistent Memory（MEMORY.md / USER.md 与 memory 工具）',
                     'url': 'https://hermes-agent.nousresearch.com/docs/user-guide/features/memory'}]
        return []

    # ---- Codex background jobs -------------------------------------------

    def _codex_job_evidence(self, path):
        db_path = os.path.join(self.codex_home, 'memories_1.sqlite')
        if not os.path.isfile(db_path):
            return {'traced': False, 'note': '未找到 memories_1.sqlite，无法读取后台任务记录。'}
        uri = 'file:%s?mode=ro' % _sqlite_uri_path(db_path)
        con = sqlite3.connect(uri, uri=True, timeout=2)
        con.row_factory = sqlite3.Row
        try:
            jobs = con.execute(
                "SELECT kind, job_key, status, started_at, finished_at, retry_remaining "
                "FROM jobs WHERE kind LIKE 'memory%' ORDER BY started_at DESC LIMIT 24").fetchall()
        finally:
            con.close()
        records = [{'kind': row['kind'], 'jobKey': row['job_key'], 'status': row['status'],
                    'startedAt': _utc(row['started_at']), 'finishedAt': _utc(row['finished_at']),
                    'retries': row['retry_remaining']} for row in jobs]
        stage1 = []
        if os.path.basename(path) != 'MEMORY.md' and 'raw_memories' not in path:
            stage1 = self._stage1_match(path)
        return {'traced': True, 'evidence': [
            {'kind': '任务记录', 'label': 'memories_1.sqlite jobs 表（只读查询）',
             'detail': '以下为最近的后台记忆任务；stage1 按 thread 逐个提取，consolidate_global 全局整理。',
             'records': records},
            *( [{'kind': '产物对照', 'label': 'stage1_outputs 产物对照',
                 'detail': '本文件的 slug 与后台提取产物对应；usage_count 为被后续会话引用次数。',
                 'records': stage1}] if stage1 else []),
        ], 'note': '后台任务调用模型时的确切提示词不落盘；此处展示任务记录与生成规则文本。'}

    def _stage1_match(self, path):
        slug = os.path.basename(path)
        if slug.endswith('.md'):
            slug = slug[:-3]
        db_path = os.path.join(self.codex_home, 'memories_1.sqlite')
        uri = 'file:%s?mode=ro' % _sqlite_uri_path(db_path)
        con = sqlite3.connect(uri, uri=True, timeout=2)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                'SELECT rollout_slug, generated_at, usage_count, last_usage, selected_for_phase2 '
                'FROM stage1_outputs WHERE rollout_slug = ? LIMIT 1', (slug,)).fetchall()
        finally:
            con.close()
        return [{'rolloutSlug': row['rollout_slug'], 'generatedAt': _epoch(row['generated_at']),
                 'usageCount': row['usage_count'], 'lastUsage': _epoch(row['last_usage']),
                 'selectedForPhase2': bool(row['selected_for_phase2'])} for row in rows]

    # ---- Codex ad-hoc notes: trace the writing tool call ------------------

    def _adhoc_trace(self, path, entry):
        name = os.path.basename(path)
        stamp = _note_stamp(name)
        budget = SessionScanBudget()
        sessions_dir = os.path.join(self.codex_home, 'sessions')
        archived_dir = os.path.join(self.codex_home, 'archived_sessions')
        candidates = []
        for base in (sessions_dir, archived_dir):
            day_hits = self._session_candidates(base, stamp, budget)
            candidates.extend(day_hits)
        if not candidates:
            candidates = self._recent_candidates(sessions_dir, entry, budget)
            candidates += self._recent_candidates(archived_dir, entry, budget)
        hits = []
        scanned = 0
        for session in candidates:
            if scanned >= MAX_SCAN_FILES:
                break
            found = self._scan_session_for_note(session, name, budget)
            scanned += 1
            if found:
                hits.extend(found)
                break
        evidence = [{'kind': '机制说明', 'label': '写入规则（来自注入提示词与扩展声明）',
                     'detail': '会话内模型只能在被用户明确要求时，通过 apply_patch 在 extensions/ad_hoc/notes/ 新增一条 '
                               '<timestamp>-<slug>.md 笔记，不得直接改写 MEMORY.md / memory_summary.md；'
                               '后台整理任务随后把笔记合并进正式记忆文件。'}]
        for hit in hits:
            evidence.append({'kind': '写入调用', 'label': '会话 %s' % os.path.basename(hit['session']),
                             'sessionPath': hit['session'], 'tool': hit['tool'],
                             'timestamp': hit.get('timestamp'),
                             'input': _clamp(hit.get('input', ''), MAX_TOOL_INPUT_CHARS),
                             'output': _clamp(hit.get('output', ''), MAX_TOOL_OUTPUT_CHARS)})
        result = {'traced': bool(hits), 'evidence': evidence}
        if not hits:
            result['note'] = ('按文件名时间戳与 mtime 窗口未在本地会话记录中找到写入调用；'
                              '会话可能已归档、清理或来自未保留记录的入口。')
        return result

    def _session_candidates(self, base, stamp, budget=None):
        budget = budget or SessionScanBudget()
        budget.check()
        if not os.path.isdir(base) or not stamp:
            return []
        wanted = None
        date = stamp[:8]
        if date.isdigit() and len(date) == 8:
            wanted = date[:4] + '/' + date[4:6] + '/' + date[6:8]
        out = []
        if wanted:
            day_dir = os.path.join(base, *wanted.split('/'))
            if os.path.isdir(day_dir):
                for name in os.listdir(day_dir):
                    budget.check()
                    full = os.path.join(day_dir, name)
                    if name.endswith('.jsonl') and os.path.isfile(full):
                        out.append(full)
        return sorted(out, key=lambda p: -os.path.getmtime(p))

    def _recent_candidates(self, base, entry, budget=None):
        budget = budget or SessionScanBudget()
        budget.check()
        if not os.path.isdir(base):
            return []
        try:
            mtime = os.stat(str(entry.get('path', ''))).st_mtime
        except OSError:
            mtime = time.time()
        cutoff = mtime - SCAN_WINDOW_DAYS * 86400
        out = []
        for root, dirs, names in os.walk(base):
            budget.check()
            dirs[:] = sorted(d for d in dirs)
            for name in names:
                budget.check()
                if not name.endswith('.jsonl'):
                    continue
                full = os.path.join(root, name)
                try:
                    info = os.stat(full)
                except OSError:
                    continue
                if cutoff <= info.st_mtime <= mtime + 86400:
                    out.append((info.st_mtime, full))
        out.sort(key=lambda item: -item[0])
        return [full for _, full in out[:MAX_SCAN_FILES]]

    def _scan_session_for_note(self, session_path, note_name, budget=None):
        budget = budget or SessionScanBudget()
        budget.check()
        outputs = {}
        calls = {}
        try:
            stream = open(session_path, 'rb')
        except OSError:
            return []
        with stream:
            index = -1
            while raw := budget.read_line(stream):
                index += 1
                if len(raw) > MAX_SESSION_LINE_BYTES:
                    # 超长记录按有界块排空，不能把剩余片段当成新记录。
                    while raw and not raw.endswith(b'\n'):
                        raw = budget.read_line(stream)
                    continue
                line = raw.decode('utf-8', errors='replace')
                interesting = note_name in line or 'ad_hoc/notes' in line
                if not interesting and '_call_output' not in line:
                    continue

                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                payload = item.get('payload') or {}
                item_type = payload.get('type')
                if item_type in ('custom_tool_call', 'function_call'):
                    text = payload.get('arguments') or payload.get('input') or ''
                    if note_name in str(text):
                        calls[payload.get('call_id')] = {
                            'tool': payload.get('name'), 'input': str(text),
                            'timestamp': item.get('timestamp'), 'line': index}
                elif item_type in ('custom_tool_call_output', 'function_call_output'):
                    outputs[payload.get('call_id')] = _output_text(payload)
        hits = []
        for call_id, call in calls.items():
            hit = dict(call, session=session_path,
                       output=outputs.get(call_id, ''))
            text = call.get('input', '')
            if not _looks_like_write(text):
                continue
            hits.append(hit)
        return hits

    # ---- Codex extension resources & rules --------------------------------

    def _extension_resource_evidence(self, kind_id):
        ext = 'skysight' if kind_id == 'codex.skysight.resource' else 'chronicle'
        instructions = os.path.join(self.codex_home, 'memories', 'extensions', ext, 'instructions.md')
        label = 'Skysight' if ext == 'skysight' else 'Chronicle'
        detail = ('%s 是后台常驻进程（%s）按 10 分钟 / 6 小时窗口切片生成的活动摘要；'
                  '生成时后台整理任务会读取这些资源作为证据。' %
                  (label, '事件流汇总' if ext == 'skysight' else '被动屏幕记录'))
        evidence = [{'kind': '机制说明', 'label': '%s 生成机制' % label, 'detail': detail}]
        if os.path.isfile(instructions):
            evidence.append({'kind': '扩展声明', 'label': 'extensions/%s/instructions.md（本机声明原文）' % ext,
                             'path': instructions})
        return {'traced': True, 'evidence': evidence}

    # ---- Codex memory skills -----------------------------------------------

    def _memory_skill_evidence(self):
        index_path = os.path.join(self.codex_home, 'memories', 'MEMORY.md')
        pointers = []
        if os.path.isfile(index_path):
            try:
                with open(index_path, encoding='utf-8', errors='replace') as stream:
                    for line in stream:
                        if 'skills/' in line and 'SKILL.md' in line:
                            pointers.append(line.strip()[:200])
                        if len(pointers) >= 8:
                            break
            except OSError:
                pass
        evidence = [{'kind': '机制说明', 'label': '记忆技能包的生成与使用',
                     'detail': '由后台整理任务从重复出现的流程中提炼（触发条件 + 输入 + 步骤 + 验证）；'
                               '不自动注入会话。使用路径：MEMORY.md 正文里的 "Related skill" 指针，'
                               '或模型检索记忆时按需打开 SKILL.md；含 disable-model-invocation: true 的'
                               '技能只能由用户以斜杠命令触发。'}]
        if pointers:
            evidence.append({'kind': '指针对照', 'label': 'MEMORY.md 中的相关技能指针',
                             'records': pointers[:8]})
        return {'traced': True, 'evidence': evidence}

    # ---- Hermes ------------------------------------------------------------

    def _hermes_note(self, kind_id):
        which = 'MEMORY.md（智能体自己的笔记）' if kind_id == 'hermes.memory' else 'USER.md（用户画像）'
        return {'traced': True, 'evidence': [
            {'kind': '机制说明', 'label': '%s 的写入机制' % which,
             'detail': '模型在会话中通过 memory 工具（add / replace / remove）直接写入，无后台整理任务；'
                       '写入立即落盘，系统提示词中的快照在下一个会话开始时刷新。'
                       '部分条目还来自每轮之后的后台自我改进审查。'}],
            'note': 'Hermes 的 memory 工具调用记录保存在会话数据库 state.db 中；'
                    '当前版本未实现逐条调用溯源。'}


def _looks_like_write(text):
    return any(token in text for token in ('apply_patch', '*** Add File:', '*** Update File:',
                                           '*** Delete File:', 'cat >', 'tee ', 'touch '))


def _output_text(payload):
    """Flatten the various tool-output shapes into plain text."""
    content = payload.get('output')

    def _one(piece):
        if isinstance(piece, dict):
            for key in ('text', 'content', 'output'):
                value = piece.get(key)
                if isinstance(value, str):
                    return value
            return json.dumps(piece, ensure_ascii=False)
        return str(piece)

    if isinstance(content, list):
        return '\n'.join(filter(None, (_one(piece) for piece in content)))
    if isinstance(content, dict):
        return _one(content)
    return str(content or '')


def _note_stamp(name):
    match = NOTE_NAME.match(name)
    return match.group('stamp') if match else ''


def _sqlite_uri_path(path):
    return path.replace('%', '%25').replace('?', '%3f').replace('#', '%23')


def _utc(value):
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')
    except (OverflowError, OSError, ValueError):
        return None


def _epoch(value):
    return _utc(value)
