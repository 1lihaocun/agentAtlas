import type { ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, queryString } from '../../shared/api/client';
import type { Asset, Page } from '../../shared/api/types';
import { categories, date } from '../../shared/lib/format';
import { useActions, useFilters } from '../../shared/lib/navigation';
import { ErrorMessage, Loading, PageTitle } from '../../shared/ui/feedback';
import { FileTable } from '../../shared/ui/FileTable';
import { Pagination } from '../../shared/ui/Pagination';
import { FilterDropdown } from '../../shared/ui/FilterDropdown';

interface AssetPage extends Page<Asset> {
  counts: { categories: Record<string, number>; platforms: Record<string, number> };
  facets: { platforms: string[]; categories: string[]; projects: string[] }; generatedAt?: number;
}

export function AssetsPage({ searchForm, searchResults }: { searchForm: ReactNode; searchResults: ReactNode }) {
  const { params, update, page, pageSize } = useFilters();
  const actions = useActions();
  const filters = { platform: params.get('platform') || '', category: params.get('category') || '', project: params.get('project') || '', sort: params.get('sort') || 'path', page, pageSize };
  const query = useQuery({ queryKey: ['assets', filters], queryFn: ({ signal }) => api<AssetPage>('/assets?' + queryString(filters), { signal }) });
  const activeSearch = Boolean(params.get('q'));
  return <><PageTitle title="全部文件"><span className="muted">更新于 {date(query.data?.generatedAt)}</span></PageTitle>
    <div className="toolbar">
      {(['platform', 'category', 'project'] as const).map((key, index) => {
        const options = query.data?.facets[(['platforms', 'categories', 'projects'] as const)[index]] || [];
        return <FilterDropdown key={key} label={['平台', '用途', '项目'][index]} value={filters[key]}
          searchable={key === 'project'} onChange={value => update({ [key]: value, page: 1, searchId: null })}
          options={[{ value: '', label: '全部' }, ...options.map(value => ({ value, label: key === 'category' ? categories[value] || value : value }))]} />;
      })}
      <FilterDropdown label="排序" value={filters.sort} onChange={value => update({ sort: value, page: 1 })}
        options={[{ value: 'path', label: '文件路径' }, { value: 'modified', label: '最近修改' }, { value: 'size', label: '文件大小' }]} />
      <button onClick={() => update({ platform: null, category: null, project: null, q: null, searchId: null, page: 1 })}>清除筛选</button>
      <span className="toolbar-spacer" />{searchForm}
    </div>{searchResults}
    {!activeSearch && <><ErrorMessage error={query.error} retry={() => void query.refetch()} />{query.isPending && <Loading />}
      {query.data && <><Pagination data={query.data} /><FileTable files={query.data.items} {...actions} /></>}
    </>}
  </>;
}
