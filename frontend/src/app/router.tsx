import { createBrowserRouter, Link, Navigate, useRouteError } from 'react-router';
import { Layout } from './Layout';

function RouteError() { const error = useRouteError(); return <main className="route-error"><h1>页面无法加载</h1><p role="alert">{error instanceof Error ? error.message : '页面地址或请求无效。'}</p><Link to="/assets">返回全部文件</Link></main>; }
export const router = createBrowserRouter([{ Component: Layout, ErrorBoundary: RouteError, children: [
  { index: true, element: <Navigate to="/assets" replace /> },
  { path: 'assets', lazy: async () => {
    const [{ AssetsPage }, { SearchForm, SearchResults }] = await Promise.all([import('../features/assets/AssetsPage'), import('../features/search/SearchPanel')]);
    return { Component: () => <AssetsPage searchForm={<SearchForm />} searchResults={<SearchResults />} /> };
  } },
  { path: 'instructions', lazy: async () => ({ Component: (await import('../features/instructions/InstructionsPage')).InstructionsPage }) },
  { path: 'instructions/user', lazy: async () => { const { InstructionsPage } = await import('../features/instructions/InstructionsPage'); return { Component: () => <InstructionsPage user /> }; } },
  { path: 'instructions/directory', lazy: async () => { const { InstructionsPage } = await import('../features/instructions/InstructionsPage'); return { Component: () => <InstructionsPage directory /> }; } },
  { path: 'memories', element: <Navigate to="/memories/scope" replace /> },
  { path: 'memories/scope', lazy: async () => ({ Component: (await import('../features/memories/MemoryPage')).MemoryPage }) },
  { path: 'memories/directory', lazy: async () => { const { MemoryPage } = await import('../features/memories/MemoryPage'); return { Component: () => <MemoryPage directory /> }; } },
  { path: 'reviews', lazy: async () => ({ Component: (await import('../features/reviews/ReviewsPage')).ReviewsPage }) },
  { path: 'duplicates', lazy: async () => ({ Component: (await import('../features/duplicates/DuplicatesPage')).DuplicatesPage }) },
  { path: 'file-types', lazy: async () => ({ Component: (await import('../features/file-guide/GuidePage')).GuidePage }) },
  { path: 'file-types/:typeId', lazy: async () => ({ Component: (await import('../features/file-guide/GuidePage')).GuidePage }) },
  { path: 'settings', element: <Navigate to="/assets" replace /> },
  { path: 'files', lazy: async () => ({ Component: (await import('../features/files/FilePage')).FilePage }) },
  { path: '*', Component: () => <><h1>页面不存在</h1><Link to="/assets">返回全部文件</Link></> },
] }]);
