import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { api, queryString } from '../../shared/api/client';
import type { MemoryAsset, Page, StorageTree } from '../../shared/api/types';
import { useActions, useFilters } from '../../shared/lib/navigation';
import { platformColor } from '../../shared/lib/format';
import { ErrorMessage, Loading, Empty, PageTitle } from '../../shared/ui/feedback';
import { Pagination } from '../../shared/ui/Pagination';
import { FilterDropdown } from '../../shared/ui/FilterDropdown';

interface MemoryPageData extends Page<MemoryAsset> {
  storageTree: StorageTree; counts: { total: number; matched: number; direct: number; subtree: number };
  platforms: string[]; profiles: string[]; projects: { path: string; name: string }[]; notice: string;
}

export function MemoryPage({ directory = false }: { directory?: boolean }) {
  const { params, update, page, pageSize } = useFilters();
  const search = params.get('q') || '';
  const [input, setInput] = useState(search);
  useEffect(() => setInput(search), [search]);
  useEffect(() => {
    if (input === search) return;
    const timer = window.setTimeout(() => update({ q: input, page: 1 }, true), 300);
    return () => window.clearTimeout(timer);
  }, [input, search, params]);
  const { openFile } = useActions();
  const path = directory ? params.get('path') || '' : '';
  const filters = { page, pageSize, path, recursive: params.get('recursive') === 'true',
    platform: params.get('platform') || '', profile: params.get('profile') || '', project: params.get('project') || '',
    level: params.get('level') || '', stage: params.get('stage') || '', q: params.get('q') || '' };
  const query = useQuery({ queryKey: ['memories', filters], queryFn: ({ signal }) => api<MemoryPageData>('/memories?' + queryString(filters), { signal }) });
  const data = query.data, node = path ? data?.storageTree.nodes[path] : undefined;
  const children = node?.children || (!path ? data?.storageTree.roots || [] : []);
  const navigateDirectory = (next: string) => update({ path: next, page: 1 });
  return <><PageTitle title={directory ? '记忆存放目录' : '记忆作用域'}>
    <Link className="button" to={directory ? '/memories/scope' : '/memories/directory'}>{directory ? '按作用域浏览' : '按存放目录浏览'}</Link>
  </PageTitle><div className="toolbar">
    <FilterDropdown label="平台" value={filters.platform} onChange={value => update({ platform: value, page: 1 })}
      options={[{ value: '', label: '全部' }, ...(data?.platforms || []).map(value => ({ value, label: value }))]} />
    <FilterDropdown label="配置档案" value={filters.profile} searchable onChange={value => update({ profile: value, page: 1 })}
      options={[{ value: '', label: '全部' }, ...(data?.profiles || []).map(value => ({ value, label: value }))]} />
    <FilterDropdown label="适用项目" value={filters.project} searchable onChange={value => update({ project: value, page: 1 })}
      options={[{ value: '', label: '全部' }, ...(data?.projects || []).map(project => ({ value: project.path, label: project.name, description: project.path }))]} />
    <FilterDropdown label="作用层级" value={filters.level} onChange={value => update({ level: value, page: 1 })}
      options={[{ value: '', label: '全部' }, ...Object.entries({ user: '用户', profile: '配置档案', workspace: '工作区', project: '项目', directory: '目录', session: '会话', unknown: '未知' }).map(([value, label]) => ({ value, label }))]} />
    <FilterDropdown label="处理阶段" value={filters.stage} onChange={value => update({ stage: value, page: 1 })}
      options={[{ value: '', label: '全部' }, { value: 'foreground', label: '前台读取' }, { value: 'background', label: '后台整合' }, { value: 'unknown', label: '未知' }]} />
    <input aria-label="搜索记忆元数据" placeholder="搜索标题、说明、路径" value={input} onChange={event => setInput(event.target.value)} />
    <button onClick={() => { setInput(''); update({ platform: null, profile: null, project: null, level: null, stage: null, q: null, page: 1 }); }}>清除筛选</button>
  </div>
    <ErrorMessage error={query.error} retry={() => void query.refetch()} />{query.isPending && <Loading />}
    {data && <><div className="list-bar"><p className="muted">{data.notice}</p><Pagination data={data} /></div>
      {directory && <>{(node?.ancestors || path ? [path] : []).length > 0 || path ? <nav className="breadcrumbs" aria-label="记忆目录位置"><button onClick={() => navigateDirectory('')}>记忆目录</button>
        {(node?.ancestors || (path ? [path] : [])).map(ancestor => <button key={ancestor} onClick={() => navigateDirectory(ancestor)}>{data.storageTree.nodes[ancestor]?.name || ancestor}</button>)}</nav> : null}
        <div className="stats"><span>本层记忆 <b>{data.counts.direct}</b></span><span>包含子目录 <b>{data.counts.subtree}</b></span><span>直接子目录 <b>{children.length}</b></span></div>
        <div className="folder-list">{children.map(child => <button className="folder" key={child} onClick={() => navigateDirectory(child)}>
          <strong>{path ? data.storageTree.nodes[child].name : data.storageTree.nodes[child].displayPath}</strong>
          <small>本层 {data.storageTree.nodes[child].directCount} · 包含子目录 {data.storageTree.nodes[child].totalCount}</small></button>)}</div>
        {path && <label className="check"><input type="checkbox" checked={filters.recursive} onChange={event => update({ recursive: event.target.checked, page: 1 })} />包含子目录文件</label>}
      </>}
      {(!directory || path) && <>{data.items.length ? <div className="memory-list">{data.items.map(file => <article key={file.path}>
        <div className="row"><button className="file-link" onClick={() => openFile(file.path)} disabled={file.searchable === false || file.restricted}>
          <strong>{file.memoryInfo?.title || file.name}</strong><span className="path">{file.path}</span></button><button onClick={() => openFile(file.path)}>查看内容</button></div>
        <div className="tags"><span className="tag platform"><span className="dot" style={{ background: platformColor(file.platform) }} />{file.platform}</span><span className="tag">{file.semantics.label}</span><span className="tag">{file.semantics.loading.label}</span>
          {file.semantics.pipeline && <span className="tag">{file.semantics.pipeline.label}</span>}<span className="tag outline">{file.semantics.observation.label}</span></div>
        {file.memoryInfo?.description && <p>{file.memoryInfo.description}</p>}
        <details><summary>读取条件与证据</summary><dl><dt>读取者</dt><dd>{file.semantics.reader}</dd><dt>触发条件</dt><dd>{file.semantics.loading.trigger}</dd>
          <dt>适用项目</dt><dd>{file.semantics.allProjects ? '规则范围内的全部项目' : file.semantics.appliesTo.join('、') || '未确认'}</dd><dt>依据</dt><dd>{file.semantics.basis.detail}</dd></dl>
          {file.memoryInfo?.relatedProjects.map(project => <p key={project.path}>内容关联：{project.path} · L{project.evidence.line}</p>)}</details>
      </article>)}</div> : <Empty>{directory ? '当前目录没有匹配的记忆，可进入子目录或修改筛选条件。' : '没有符合条件的记忆。'}</Empty>}</>}
    </>}
  </>;
}
