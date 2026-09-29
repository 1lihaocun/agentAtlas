import { useEffect } from 'react';
import { useSearchParams } from 'react-router';
import type { Page } from '../api/types';
import { FilterDropdown } from './FilterDropdown';

export function Pagination({ data }: { data: Pick<Page<unknown>, 'page' | 'pageSize' | 'total'> }) {
  const [params, setParams] = useSearchParams();
  const pages = Math.max(1, Math.ceil(data.total / data.pageSize));
  const requested = params.get('page');
  useEffect(() => {
    if (requested !== null && Number(requested) !== data.page) {
      setParams(current => { const next = new URLSearchParams(current); next.set('page', String(data.page)); return next; }, { replace: true });
    }
  }, [data.page, requested, setParams]);
  function go(page: number, pageSize = data.pageSize) {
    setParams(current => { const next = new URLSearchParams(current); next.set('page', String(page)); next.set('pageSize', String(pageSize)); return next; });
  }
  return <div className="pagination" aria-label="列表分页">
    <span>共 {data.total} 条 · 第 {data.page} / {pages} 页</span>
    <FilterDropdown label="每页数量" value={String(data.pageSize)} onChange={value => go(1, Number(value))}
      options={[25, 50, 100].map(size => ({ value: String(size), label: `${size} 条 / 页` }))} />
    <button disabled={data.page <= 1} onClick={() => go(data.page - 1)}>上一页</button>
    <button disabled={data.page >= pages} onClick={() => go(data.page + 1)}>下一页</button>
  </div>;
}
