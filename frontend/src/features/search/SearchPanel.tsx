import { useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { X } from 'lucide-react';
import { api, post, queryString } from '../../shared/api/client';
import type { FileDocument, Page, SearchHit } from '../../shared/api/types';
import { useFilters } from '../../shared/lib/navigation';
import { ErrorMessage, Loading, Empty } from '../../shared/ui/feedback';
import { Pagination } from '../../shared/ui/Pagination';
import { ConfirmDialog } from '../../shared/ui/ConfirmDialog';
import { FilterDropdown } from '../../shared/ui/FilterDropdown';
import { IndexControls } from './IndexControls';

function useSearchFilters() {
  const { params } = useFilters();
  return { platform: params.get('platform') || '', category: params.get('category') || '', project: params.get('project') || '' };
}

export function SearchForm() {
  const { params, update } = useFilters();
  const q = params.get('q') || '', mode = params.get('mode') || 'keyword';
  const [input, setInput] = useState(q);
  useEffect(() => setInput(q), [q]);
  const [confirming, setConfirming] = useState(false);
  const filters = useSearchFilters();
  const cloud = useMutation({ mutationFn: (text: string) => post<{ searchId: string }>('/search/execute', { q: text, mode, filters, confirmCloud: true }),
    onSuccess: (result, text) => { setConfirming(false); update({ q: text, searchId: result.searchId, page: 1 }); } });
  function submit() {
    if (!input.trim()) { update({ q: null, searchId: null, page: 1 }); return; }
    if (mode === 'keyword') update({ q: input.trim(), searchId: null, page: 1 });
    else setConfirming(true);
  }
  return <form className="search-inline" onSubmit={event => { event.preventDefault(); submit(); }}>
    <span className="search-box">
      <input aria-label="搜索文件正文" placeholder="搜索文件正文、路径或名称" value={input} onChange={event => setInput(event.target.value)} />
      {input && <button type="button" className="search-clear" aria-label="清空搜索输入" onClick={() => setInput('')}><X size={13} aria-hidden="true" /></button>}
    </span>
    <FilterDropdown label="检索模式" value={mode} onChange={value => update({ mode: value, searchId: null, page: 1 })}
      options={[{ value: 'keyword', label: '本地关键词' }, { value: 'vector', label: '语义检索' }, { value: 'hybrid', label: '混合检索' }]} />
    <button className="primary" disabled={cloud.isPending}>搜索</button>
    {q && <button type="button" onClick={() => { setInput(''); update({ q: null, searchId: null, line: null, page: 1 }); }}>清除搜索</button>}
    <IndexControls />
    {q && mode !== 'keyword' && !params.get('searchId') && <span className="muted">检索条件：{q}。请提交搜索，确认发送检索文本。</span>}
    <ErrorMessage error={cloud.error} />
    <ConfirmDialog open={confirming} onOpenChange={setConfirming} title="确认云端检索" pending={cloud.isPending} onConfirm={() => cloud.mutate(input.trim())}>
      将检索文本“{input.trim()}”发送到已配置的 embedding 服务。文件正文的向量化由独立索引操作控制。
    </ConfirmDialog>
  </form>;
}

export function SearchResults() {
  const { params, update, page, pageSize } = useFilters();

  const q = params.get('q') || '', mode = params.get('mode') || 'keyword';
  const searchId = params.get('searchId');
  const filters = useSearchFilters();
  const [expanded, setExpanded] = useState<Record<string, FileDocument>>({});
  const expand = useMutation({ mutationFn: (hit: SearchHit) => api<FileDocument>('/file?' + queryString({ path: hit.path })),
    onSuccess: (doc, hit) => setExpanded(current => ({ ...current, [hit.path + '#' + hit.startLine]: doc })) });
  const query = useQuery({ queryKey: ['search', q, mode, filters, page, pageSize, searchId], enabled: Boolean(q && (mode === 'keyword' || searchId)),
    queryFn: ({ signal }) => api<Page<SearchHit>>(mode === 'keyword' ? '/search?' + queryString({ q, ...filters, page, pageSize }) : `/search/results/${searchId}?` + queryString({ page, pageSize }), { signal }) });
  useEffect(() => { setExpanded({}); expand.reset(); }, [q, searchId]);
  function toggleChunk(hit: SearchHit) {
    const key = hit.path + '#' + hit.startLine;
    if (expanded[key]) { setExpanded(current => { const next = { ...current }; delete next[key]; return next; }); return; }
    expand.mutate(hit);
  }
  function openHit(hit: SearchHit) { update({ file: hit.path, line: hit.startLine, panel: 'raw', section: null }); }
  if (!q) return null;
  return <div className="search-area">
    <ErrorMessage error={query.error || expand.error} />
    {query.isFetching && <Loading />}
    {query.data && <><Pagination data={query.data} /><p className="muted">结果按命中片段计数，同一个文件可以包含多个片段。</p>
      {query.data.items.length ? <div className="result-list">{query.data.items.map((hit, index) => {
        const key = hit.path + '#' + hit.startLine;
        const doc = expanded[key];
        const lines = doc?.content.split('\n');
        return <article key={`${hit.path}:${hit.startLine}:${index}`}>
        <button className="file-link" onClick={() => openHit(hit)}><strong>{hit.name} · L{hit.startLine}–{hit.chunkEndLine}</strong><span className="path">{hit.path}</span></button>
        <pre dangerouslySetInnerHTML={{ __html: hit.snippet }} />
        <div className="toolbar">
          <button type="button" disabled={expand.isPending} onClick={() => toggleChunk(hit)}>{doc ? '收起整块' : `展开整块（L${hit.startLine}–${hit.chunkEndLine}）`}</button>
          <button type="button" onClick={() => openHit(hit)}>在查看器中定位 L{hit.startLine}</button>
        </div>
        {doc && lines && <pre className="chunk-full">{doc.truncated && hit.chunkEndLine >= lines.length
          ? '该片段超出文件预览范围，无法完整展开。请使用外部编辑器查看源文件。'
          : lines.slice(hit.startLine - 1, hit.chunkEndLine).join('\n')}</pre>}
        </article>;
      })}</div> : <Empty />}</>}
  </div>;
}
