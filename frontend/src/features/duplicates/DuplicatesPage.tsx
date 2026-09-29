import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, post, queryString } from '../../shared/api/client';
import type { Asset, FileDocument, Page } from '../../shared/api/types';
import { useActions, useFilters } from '../../shared/lib/navigation';
import { ErrorMessage, Loading, Empty, PageTitle } from '../../shared/ui/feedback';
import { Pagination } from '../../shared/ui/Pagination';
import { ConfirmDialog } from '../../shared/ui/ConfirmDialog';

interface Group { id: string; files: Asset[] }
interface Result { synced: number; failed: number; results: { path: string; ok: boolean; error?: string }[] }
export function DuplicatesPage() {
  const { params, page, pageSize } = useFilters(); const actions = useActions(); const client = useQueryClient();
  const [source, setSource] = useState<FileDocument | null>(null);
  const query = useQuery({ queryKey: ['duplicates', page, pageSize, params.get('group')], queryFn: () => api<Page<Group>>('/duplicates?' + queryString({ page, pageSize, group: params.get('group') })) });
  const prepare = useMutation({ mutationFn: (path: string) => api<FileDocument>('/file?' + queryString({ path })), onSuccess: setSource });
  const sync = useMutation({ mutationFn: () => post<Result>('/sync-dups', { source: source?.path, baseVersion: source?.version }), onSuccess: () => {
    setSource(null); void client.invalidateQueries({ queryKey: ['duplicates'] });
  } });
  return <><PageTitle title="重复文件" /><p className="muted">按扫描时的相同内容分组。同步前检查每份文件的版本，并为修改创建备份。</p>
    <ErrorMessage error={query.error || prepare.error || sync.error} retry={() => void query.refetch()} />{query.isPending && <Loading />}
    {sync.data && <section className="panel"><p>同步成功 {sync.data.synced} 份，失败 {sync.data.failed} 份。</p>{sync.data.results.map(row => <p key={row.path}>{row.path}：{row.ok ? '已完成' : row.error}</p>)}</section>}
    {query.data && <><Pagination data={query.data} />{query.data.items.map(group => <section className="panel" key={group.id}><h2>{group.files.length} 份相同文件</h2>
      {group.files.map(file => <div className="duplicate-row" key={file.path}><button className="file-name" onClick={() => actions.openFile(file.path)}>{file.path}</button>
        <button disabled={prepare.isPending} onClick={() => prepare.mutate(file.path)}>以此文件同步</button></div>)}</section>)}{!query.data.total && <Empty>当前没有重复文件。</Empty>}</>}
    <ConfirmDialog open={Boolean(source)} onOpenChange={open => { if (!open) setSource(null); }} title="同步重复文件" pending={sync.isPending} onConfirm={() => sync.mutate()}>
      使用 {source?.path} 的当前内容更新同组副本。已经被外部修改的目标会返回冲突。<ErrorMessage error={sync.error} /></ConfirmDialog>
  </>;
}
