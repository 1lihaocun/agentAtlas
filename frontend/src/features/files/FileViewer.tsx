import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, post, queryString } from '../../shared/api/client';
import type { FileDocument, SaveResult, SectionDocument } from '../../shared/api/types';
import { useFilters } from '../../shared/lib/navigation';
import { ErrorMessage, Loading } from '../../shared/ui/feedback';
import { GuideDetail } from '../../shared/ui/GuideDetail';
import { ConfirmDialog } from '../../shared/ui/ConfirmDialog';
import { FilterDropdown } from '../../shared/ui/FilterDropdown';
import { useDrafts } from './Drafts';
import type { Guide } from '../../shared/api/types';

export function FileViewer({ path, context, close }: { path: string; context: ReactNode; close: () => void }) {
  const { params, update } = useFilters();
  const panel = params.get('panel') || 'raw';
  const query = useQuery({ queryKey: ['file', path], queryFn: () => api<FileDocument>('/file?' + queryString({ path })) });
  const guideQuery = useQuery({ queryKey: ['guide', path], enabled: panel === 'guide', queryFn: () => api<Guide>('/file-guide?' + queryString({ path })) });
  const client = useQueryClient();
  const drafts = useDrafts();
  const { initialize } = drafts;
  const [discard, setDiscard] = useState(false);
  const [adoptVersion, setAdoptVersion] = useState(false);
  const editor = useRef<HTMLTextAreaElement>(null);
  const positionedLine = useRef<number | null>(null);
  const draft = drafts.entries[path];
  const gotoLine = Number(params.get('line')) || 0;
  useEffect(() => { if (query.data?.version) initialize(path, query.data.content, query.data.version); }, [query.data, path, initialize]);
  useEffect(() => {
    const input = editor.current;
    if (input && draft) { input.setSelectionRange(draft.selection, draft.selection); input.scrollTop = draft.scroll; }
    // 光标和滚动位置只在重新打开文件或编辑面板时恢复。
  }, [path, panel, Boolean(draft)]);
  useEffect(() => {
    const input = editor.current;
    if (!gotoLine) { positionedLine.current = null; return; }
    if (!input || panel !== 'raw' || !query.data || (query.data.version && !draft) || positionedLine.current === gotoLine) return;
    positionedLine.current = gotoLine;
    if (draft && draft.text !== draft.original) return;
    const target = gotoLine - 1;

    if (input.focus) input.focus();
    const lines = (draft?.text ?? query.data.content).split('\n');
    const start = lines.slice(0, target).reduce((acc, line) => acc + line.length + 1, 0);
    input.setSelectionRange(start, start + (lines[target]?.length ?? 0));
    input.scrollTop = Math.max(0, (target - 3) * parseFloat(getComputedStyle(input).lineHeight));
    // 搜索深链只定位一次；手动滚动后不再重复跳转。
  }, [gotoLine, panel, query.data, draft]);
  const save = useMutation({ mutationFn: (value: { text: string; version: string }) => post<SaveResult>('/save', {
    path, content: value.text, baseVersion: value.version,
  }), onSuccess: (result, value) => { drafts.saved(path, value.text, result.version || result.sha256); void client.invalidateQueries({ queryKey: ['file', path] }); },
    onError: () => { void query.refetch(); } });
  const openEditor = useMutation({ mutationFn: () => post('/open', { path }) });
  const dirty = draft && draft.text !== draft.original;
  const panelTabs: [string, string][] = [['raw', '文件内容'], ['sections', '章节编辑'], ['translation', '中文翻译'], ['guide', '文件说明']];
  return <aside className="file-viewer" aria-label="文件查看器">
    <header><strong>{path.split('/').pop()}</strong><button aria-label="关闭文件查看器" onClick={close}>关闭</button></header>
    <p className="path">{path}</p>{context}
    <nav className="tabs">{panelTabs.map(([value, label]) =>
      <button key={value} aria-pressed={panel === value} onClick={() => update({ panel: value })}>{label}</button>)}</nav>
    <ErrorMessage error={query.error} retry={() => void query.refetch()} />{query.isPending && <Loading />}
    {query.data && panel === 'raw' && <>
      <div className="toolbar"><span>{dirty ? '存在未保存修改' : query.data.editable ? '内容已保存' : '只读文件'}</span>
        <button disabled={!query.data.editable || !dirty || save.isPending} onClick={() => draft && save.mutate({ text: draft.text, version: draft.version })}>保存文件</button>
        <button disabled={!dirty} onClick={() => setDiscard(true)}>放弃修改</button>
        <button disabled={query.isFetching} onClick={() => void query.refetch()}>重新读取文件</button>
        <button disabled={!query.data.editable || openEditor.isPending} onClick={() => openEditor.mutate()}>外部编辑器</button></div>
      <ErrorMessage error={save.error || openEditor.error} />
      {draft && query.data.version !== draft.version && <section className="notice"><p>源文件已经更新。请查看最新内容，再决定如何处理草稿。</p>
        <details><summary>查看最新源文件</summary><pre className="evidence">{query.data.content}</pre></details>
        <button onClick={() => setAdoptVersion(true)}>以最新版本作为保存依据</button></section>}
      {query.data.readOnlyReason && <p className="muted">{query.data.readOnlyReason}</p>}
      {query.data.truncated && <p className="notice">文件内容过长，当前显示部分内容。</p>}
      <textarea ref={editor} aria-label="文件内容" className="code-editor" readOnly={!query.data.editable || !draft}
        value={draft?.text ?? query.data.content} spellCheck={false}
        onChange={event => drafts.update(path, { text: event.target.value, selection: event.target.selectionStart })}
        onSelect={event => { if (draft) drafts.update(path, { selection: event.currentTarget.selectionStart }); }}
        onScroll={event => { if (draft) drafts.update(path, { scroll: event.currentTarget.scrollTop }); }} />
    </>}
    {query.data && panel === 'sections' && <Sections path={path} rawDirty={Boolean(dirty)} />}
    {query.data && panel === 'translation' && <Translation path={path} version={query.data.version} />}
    {panel === 'guide' && <>{guideQuery.error && <ErrorMessage error={guideQuery.error} />}{guideQuery.isPending && <Loading />}
      {guideQuery.data && <GuideDetail guide={guideQuery.data} />}</>}
    <ConfirmDialog open={discard} onOpenChange={setDiscard} title="放弃当前文件的未保存修改" onConfirm={() => {
      drafts.discard(path); setDiscard(false); if (query.data?.version) initialize(path, query.data.content, query.data.version);
    }}>当前文件的编辑内容将恢复为最近读取的版本。</ConfirmDialog>
    <ConfirmDialog open={adoptVersion} onOpenChange={setAdoptVersion} title="更新草稿的版本依据" onConfirm={() => {
      if (query.data?.version) drafts.update(path, { version: query.data.version, original: query.data.content });
      save.reset(); setAdoptVersion(false);
    }}>保留当前草稿内容。下次保存将使用刚刚展示的源文件版本进行检查，并写入草稿内容。</ConfirmDialog>
  </aside>;
}

function Sections({ path, rawDirty }: { path: string; rawDirty: boolean }) {
  const query = useQuery({ queryKey: ['sections', path], queryFn: () => api<SectionDocument>('/sections?' + queryString({ path })) });
  const { params, update } = useFilters();
  const selected = query.data?.sections.find(section => section.id === params.get('section')) || query.data?.sections[0];
  const key = `${path}#${selected?.id}`;
  const drafts = useDrafts(); const { initialize } = drafts;
  const client = useQueryClient();
  useEffect(() => { if (selected && query.data) initialize(key, selected.text, query.data.version, { path, sectionId: selected.id }); }, [selected, query.data, key, path, initialize]);
  const draft = drafts.entries[key];
  const save = useMutation({ mutationFn: (value: { text: string; version: string; id: string; key: string; startLine: number }) => post<SaveResult>('/section/save', {
    path, text: value.text, sectionId: value.id, baseVersion: value.version,
  }), onSuccess: async (result, value) => {
    drafts.saved(value.key, value.text, result.version || result.sha256);
    const refreshed = await query.refetch();
    const replacement = refreshed.data?.sections.find(section => section.start_line <= value.startLine && section.end_line >= value.startLine);
    if (replacement) update({ section: replacement.id });
    void client.invalidateQueries({ queryKey: ['file', path] });
  } });
  return <><ErrorMessage error={query.error || save.error} retry={() => void query.refetch()} />{query.isPending && <Loading />}
    {query.data && <><FilterDropdown label="选择章节" value={selected?.id || ''} searchable onChange={value => update({ section: value })}
      options={query.data.sections.map(section => ({ value: section.id, label: section.title || '文件开头' }))} />
      {rawDirty && <p className="notice">请保存文件内容中的修改后再保存章节。</p>}
      <button disabled={rawDirty || !query.data.editable || !draft || draft.text === draft.original || save.isPending}
        onClick={() => selected && draft && save.mutate({ text: draft.text, version: draft.version, id: selected.id, key, startLine: selected.start_line })}>保存章节</button>
      <textarea className="code-editor" aria-label="章节内容" value={draft?.text || ''} readOnly={!query.data.editable}
        onChange={event => drafts.update(key, { text: event.target.value })} /></>}
  </>;
}

function Translation({ path, version }: { path: string; version: string | null }) {
  const [confirm, setConfirm] = useState(false);
  const mutation = useMutation({ mutationFn: () => post<{ translated: string; source: string }>('/translate', {
    path, baseVersion: version, lang: 'zh-CN', confirmCloud: true,
  }), onSuccess: () => setConfirm(false) });
  return <><p className="muted">翻译服务、模型与密钥统一在项目根目录的 .env 中配置（ATLAS_TRANSLATION_*）。</p>
    <button disabled={!version || mutation.isPending} onClick={() => setConfirm(true)}>翻译为中文</button>
    <ErrorMessage error={mutation.error} />{mutation.data && <pre className="translation">{mutation.data.translated}</pre>}
    <ConfirmDialog open={confirm} onOpenChange={setConfirm} title="发送文件进行翻译" pending={mutation.isPending} onConfirm={() => mutation.mutate()}>
      文件 {path} 的完整内容将发送给配置的翻译服务。翻译结果保留在查看器中。
      <ErrorMessage error={mutation.error} />
    </ConfirmDialog></>;
}
