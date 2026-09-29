import type { ReactNode } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router';

export function ErrorMessage({ error, retry }: { error: Error | null; retry?: () => void }) {
  if (!error) return null;
  return <div className="error" role="alert">{error.message}{retry && <button onClick={retry}>重新加载</button>}</div>;
}
export function Loading() { return <p className="muted" role="status">正在加载…</p>; }
export function Empty({ children = '没有符合条件的文件。' }: { children?: ReactNode }) {
  return <div className="empty">{children}</div>;
}
export function PageTitle({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="page-title"><div className="page-title-main"><BackButton /><h1>{title}</h1></div><div>{children}</div></div>;
}
function BackButton() {
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  // 只有存在应用内上级（目录下钻、文件查看器、说明详情）时才显示返回；根部页面的返回是死控件。
  const fileParam = location.pathname === '/files' ? 'path' : 'file';
  const hasParent = Boolean(params.get('path') || params.get(fileParam)) || location.pathname.startsWith('/file-types/');
  if (!hasParent) return null;
  return <button className="back-btn" aria-label="返回上一页" onClick={() => navigate(-1)}>返回</button>;
}
