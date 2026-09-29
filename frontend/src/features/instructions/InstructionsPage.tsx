import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import { api, queryString } from '../../shared/api/client';
import type { Asset, InstructionTree } from '../../shared/api/types';
import { useActions, useFilters } from '../../shared/lib/navigation';
import { basename, bytes, date } from '../../shared/lib/format';
import { ErrorMessage, Loading, Empty, PageTitle } from '../../shared/ui/feedback';
import { FileTable } from '../../shared/ui/FileTable';
import { DirectoryTree } from '../../shared/ui/DirectoryTree';
import { Pagination } from '../../shared/ui/Pagination';

interface Effective { files: { path: string; indexed: boolean }[]; assumptions?: string[]; warnings?: string[] }

export function InstructionsPage({ user = false, directory = false }: { user?: boolean; directory?: boolean }) {
  const { params, update, page, pageSize } = useFilters();
  const actions = useActions();
  const path = directory ? params.get('path') || '' : '';
  const hide = params.get('hideWorktrees') === 'true';
  const query = useQuery({ queryKey: ['instructions'], queryFn: ({ signal }) => api<InstructionTree>('/tree', { signal }) });
  const effective = useQuery({ queryKey: ['effective', path], enabled: Boolean(path && params.get('effective') === 'true'), queryFn: ({ signal }) => api<Effective>('/effective?' + queryString({ dir: path }), { signal }) });
  const all = query.data?.files.filter(file => (!hide || !file.worktree) && (!user || file.scope === 'user')) || [];
  const selected = all.filter(file => !path || file.path.startsWith(path + '/'));
  const direct = path ? selected.filter(file => file.dir === path) : user ? all : [];
  const worktrees = user || path ? [] : all.filter(file => file.worktree);
  const regular = all.filter(file => !worktrees.includes(file));
  const folders = path ? [...new Set(selected.flatMap(file => {
    const relative = file.path.slice(path.length + 1).split('/');
    return relative.length > 1 ? [path + '/' + relative[0]] : [];
  }))].sort() : [...new Set(regular.map(file => file.projectPath || file.dir!).filter(Boolean))].sort((a, b) => folderFiles(b).length - folderFiles(a).length || a.localeCompare(b));
  const worktreeFolders = [...new Set(worktrees.map(file => file.projectPath || file.dir!).filter(Boolean))].sort();
  function folderFiles(folder: string) { return all.filter(file => file.path.startsWith(folder + '/')); }
  const collapsed = !path && !user && params.get('expandWorktrees') !== 'true' && worktreeFolders.length > 0;
  const view = params.get('view') === 'list' ? 'list' : 'grid';
  // 列表视图直接对文件按路径排序：同目录天然相邻，且每份文件只出现一次（目录之间存在嵌套，按目录展开会重复）。
  const listFiles: Asset[] = view === 'list' ? [...selected].sort((a, b) => a.path.localeCompare(b.path)) : [];
  const pageFolders = folders;
  const pagedItems = view === 'list' ? listFiles.length : pageFolders.length + direct.length;
  const pageData = { page: Math.min(page, Math.max(1, Math.ceil(pagedItems / pageSize))), pageSize, total: pagedItems };
  const start = (pageData.page - 1) * pageSize;
  const visible: Asset[] = view === 'list' ? listFiles.slice(start, start + pageSize) : direct.slice(Math.max(0, start - pageFolders.length), Math.max(0, start + pageSize - pageFolders.length));
  const folderRows = pageFolders.slice(start, start + pageSize).map(folder => ({ folder, files: folderFiles(folder) }));
  const shownFolders = view === 'grid' ? folderRows : [];
  return <><PageTitle title={user ? '用户级指令' : '指令地图'}><label className="check"><input type="checkbox" checked={hide} onChange={event => update({ hideWorktrees: event.target.checked, page: 1 })} />隐藏 worktree 副本</label></PageTitle>
    <div className="toolbar"><span className="muted">目录排序：{path ? '按名称' : '按文件数'}</span>
      <nav className="view-switch" aria-label="展示方式">
        <button aria-pressed={view === 'grid'} onClick={() => update({ view: null, page: 1 })}>卡片</button>
        <button aria-pressed={view === 'list'} onClick={() => update({ view: 'list', page: 1 })}>列表</button>
      </nav>
      {collapsed && <button onClick={() => update({ expandWorktrees: true, page: 1 })}>展开 worktree 副本（{worktreeFolders.length} 个目录 · {worktrees.length} 份文件）</button>}
      <span className="muted">{view === 'list' ? '目录树：点击 ▸ 逐级展开' : '卡片按目录分组，点击进入目录'}</span>
    </div>
    <ErrorMessage error={query.error} retry={() => void query.refetch()} />{query.isPending && <Loading />}
    {query.data && <><div className="list-bar"><div className="stats"><span>文件 <b>{selected.length}</b></span>{!user && <span>项目目录 <b>{folders.length}</b></span>}{!user && worktreeFolders.length > 0 && <span>worktree 目录 <b>{worktreeFolders.length}</b></span>}<span>总大小 <b>{bytes(selected.reduce((sum, f) => sum + f.bytes, 0))}</b></span><span>扫描时间 {date(query.data.generatedAt)}</span></div><Pagination data={pageData} /></div>
      {(user || path) && <nav className="breadcrumbs"><Link to="/instructions">指令地图</Link>{path && <><Link to={'/instructions/directory?' + queryString({ path: path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '', hideWorktrees: hide })}>上级目录</Link><span className="path">{path}</span></>}</nav>}
      {path && <button onClick={() => update({ effective: params.get('effective') === 'true' ? null : true })}>查看 Codex 读取规则</button>}
      <ErrorMessage error={effective.error} />{effective.isFetching && <Loading />}{effective.data && params.get('effective') === 'true' && <div className="panel"><p className="muted">根据当前目录与规则推算。</p>{effective.data.files.map(file => <p key={file.path}><button disabled={!file.indexed} onClick={() => actions.openFile(file.path)}>{file.path}</button></p>)}</div>}
      <div className="folder-list">{shownFolders.map(({ folder, files }) => <Link className="folder" key={folder} to={'/instructions/directory?' + queryString({ path: folder, hideWorktrees: hide })} title={folder}>
        <strong>{basename(folder)}</strong><span className="path">{folder}</span><small>{files.length} 份文件 · {bytes(files.reduce((sum, f) => sum + f.bytes, 0))}</small></Link>)}</div>
      {view === 'grid' && visible.length > 0 && <FileTable files={visible} {...actions} />}
      {view === 'list' && <DirectoryTree files={selected} openFile={actions.openFile} openGuide={actions.openGuide} />}
      {!folders.length && !direct.length && <Empty>当前目录没有可显示的指令。</Empty>}
    </>}
  </>;
}
