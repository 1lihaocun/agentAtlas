import { Suspense, useCallback, useState } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate, useSearchParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import { DraftProvider, useDrafts } from '../features/files/Drafts';
import { FileViewer } from '../features/files/FileViewer';
import { JobControls } from '../features/jobs/JobControls';
import { api, queryString } from '../shared/api/client';
import type { FileContext } from '../shared/api/types';
import { Loading, ErrorMessage } from '../shared/ui/feedback';

const navGroups: { title: string; links: [string, string][] }[] = [
  { title: '浏览', links: [['/assets', '全部文件'], ['/instructions', '指令地图'], ['/memories/scope', '记忆']] },
  { title: '维护', links: [['/reviews', '淘汰审阅'], ['/duplicates', '重复文件']] },
  { title: '参考', links: [['/file-types', '文件说明']] },
];
const groupActive = (pathname: string, to: string) =>
  to === '/assets' ? pathname === '/' || pathname.startsWith('/assets') || pathname.startsWith('/files')
    : to === '/instructions' ? pathname.startsWith('/instructions')
    : to === '/memories/scope' ? pathname.startsWith('/memories')
    : pathname.startsWith(to);
export function Layout() { return <DraftProvider><Shell /></DraftProvider>; }
function Shell() {
  const [params, setParams] = useSearchParams(); const location = useLocation(); const navigate = useNavigate();
  const [notice, setNotice] = useState(''); const drafts = useDrafts();
  const file = location.pathname === '/files' ? params.get('path') : params.get('file');
  const openFile = useCallback((path: string) => setParams(current => { const next = new URLSearchParams(current); next.set(location.pathname === '/files' ? 'path' : 'file', path); next.delete('section'); return next; }), [setParams, location.pathname]);
  const openGuide = useCallback((path: string) => navigate('/file-types?' + queryString({ path })), [navigate]);
  const dirty = Object.entries(drafts.entries).filter(([, draft]) => draft.text !== draft.original);
  return <div className="app"><aside className="sidebar"><Link className="brand" to="/assets">AgentAtlas<span>本地 Agent 文件管理</span></Link>
    <nav aria-label="主导航">{navGroups.map(group => <section className="nav-group" key={group.title}>
      <h4>{group.title}</h4>
      {group.links.map(([to, label]) => <NavLink key={to} to={to} end={to === '/instructions'} className={groupActive(location.pathname, to) ? 'active' : undefined}>{label}</NavLink>)}
    </section>)}</nav>
    {dirty.length > 0 && <section className="draft-list"><strong>未保存修改 {dirty.length}</strong>{dirty.map(([key, draft]) => <button key={key} onClick={() => setParams(current => {
      const next = new URLSearchParams(current);
      next.set(location.pathname === '/files' ? 'path' : 'file', draft.path);
      next.set('panel', draft.sectionId ? 'sections' : 'raw'); next.delete('line');
      if (draft.sectionId) next.set('section', draft.sectionId); else next.delete('section');
      return next;
    })}>{draft.path.split('/').slice(-2).join('/')}</button>)}</section>}
  </aside><div className="workspace"><div className="topbar"><JobControls /></div>
    {notice && <div className="toast" role="status">{notice}<button onClick={() => setNotice('')}>关闭通知</button></div>}
    <div className={'content' + (file ? ' with-viewer' : '')}><main><Suspense fallback={<Loading />}><Outlet context={{ openFile, openGuide, notify: setNotice }} /></Suspense></main>
      {file && <FileViewer key={file} path={file} context={<ContextPanel path={file} />} close={() => {
        if (location.pathname === '/files') navigate('/assets');
        else setParams(current => { const next = new URLSearchParams(current); next.delete('file'); next.delete('panel'); next.delete('section'); next.delete('line'); return next; });
      }} />}</div></div></div>;
}
function ContextPanel({ path }: { path: string }) {
  const query = useQuery({ queryKey: ['context', path], queryFn: () => api<FileContext>('/file/context?' + queryString({ path })) });
  const storage = query.data?.memory?.memoryStorage;
  return <div className="file-context"><ErrorMessage error={query.error} />{query.data && <><span>{query.data.fileGuide.label} · {query.data.fileGuide.purpose}</span>
    <div className="toolbar">{storage && <Link to={'/memories/directory?' + queryString({ path: storage.directory, file: path })}>所在目录</Link>}</div></>}</div>;
}
