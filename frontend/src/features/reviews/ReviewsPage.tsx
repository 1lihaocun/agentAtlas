import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, post, queryString } from '../../shared/api/client';
import type { Asset, Page } from '../../shared/api/types';
import { useActions, useFilters } from '../../shared/lib/navigation';
import { ErrorMessage, Loading, Empty, PageTitle } from '../../shared/ui/feedback';
import { Pagination } from '../../shared/ui/Pagination';
import { FilterDropdown } from '../../shared/ui/FilterDropdown';

interface Review extends Asset { version: string | null; bucket: string; reasonText: string; estimatedSessions: number | null; confirmedReads: number | null; mentions: number | null }
interface Queue extends Page<Review> { counts: Record<string, number>; sourceStatus: string; warnings?: string[] }
interface Evidence { sourceStatus: string; estimatedSessions: number; confirmedReads: number; mentions: number; warnings: string[];
  assumptions: string[]; evidence: Record<string, unknown>[] }
const labels: Record<string, string> = { review: '等待审阅', active: '有使用记录', recent: '最近修改', kept: '已保留', snoozed: '已延后', unknown: '证据不足', excluded: '排除范围' };
export function ReviewsPage() {
  const { params, update, page, pageSize } = useFilters(); const actions = useActions(); const client = useQueryClient();
  const bucket = params.get('bucket') || 'review', days = Number(params.get('days') || 30), staleDays = Number(params.get('staleDays') || 90);
  if (!Number.isSafeInteger(days) || days < 1 || days > 365 || !Number.isSafeInteger(staleDays) || staleDays < 1 || staleDays > 3650) {
    throw new Error('审阅天数无效：使用记录窗口须为 1–365 天，未修改时间须为 1–3650 天。');
  }
  const query = useQuery({ queryKey: ['reviews', bucket, days, staleDays, page, pageSize], queryFn: () => api<Queue>('/retirement?' + queryString({ bucket, days, staleDays, page, pageSize })) });
  const evidencePath = params.get('evidence') || '';
  const evidence = useQuery({ queryKey: ['evidence', evidencePath, days], enabled: Boolean(evidencePath), queryFn: () => api<Evidence>('/usage/evidence?' + queryString({ path: evidencePath, days })) });
  const mutation = useMutation({ mutationFn: ({ file, action }: { file: Review; action: string }) => post('/retirement/decision', {
    path: file.path, baseVersion: file.version, action, ...(action === 'snooze' ? { snoozeDays: 30 } : {}),
  }), onSuccess: () => { void client.invalidateQueries({ queryKey: ['reviews'] }); actions.notify('审阅决定已保存'); } });
  return <><PageTitle title="淘汰审阅" /><p className="muted">结合修改时间与 Codex 本地记录审阅指令文件。证据不足的文件单独显示。</p>
    <div className="filters"><FilterDropdown label="使用记录窗口" value={String(days)} onChange={value => update({ days: value, page: 1 })}
      options={[7, 30, 90, 180, 365].map(value => ({ value: String(value), label: `${value} 天` }))} />
      <FilterDropdown label="未修改时间" value={String(staleDays)} onChange={value => update({ staleDays: value, page: 1 })}
        options={[30, 60, 90, 180, 365].map(value => ({ value: String(value), label: `${value} 天` }))} /></div>
    <nav className="tabs">{Object.entries(labels).map(([value, label]) => <button key={value} aria-pressed={bucket === value} onClick={() => update({ bucket: value, page: 1 })}>{label} {query.data?.counts[value] ?? ''}</button>)}</nav>
    {query.data && <div className="notice"><p>来源状态：{query.data.sourceStatus}</p>{query.data.warnings?.map(warning => <p key={warning}>{warning}</p>)}</div>}
    <ErrorMessage error={query.error || mutation.error} retry={() => void query.refetch()} />{query.isPending && <Loading />}
    {query.data && <><Pagination data={query.data} /><div className="cards">{query.data.items.map(file => <article className="panel" key={file.path}>
      <button className="file-name" onClick={() => actions.openFile(file.path)}>{file.path}</button><p>{file.reasonText}</p>
      <div className="toolbar"><button onClick={() => update({ evidence: file.path })}>查看使用证据</button>
        {file.version && !['excluded', 'unknown'].includes(file.bucket) && <>{[['keep', '保留当前版本'], ['snooze', '延后 30 天'], ['reset', '清除决定']].map(([action, label]) =>
          <button key={action} disabled={mutation.isPending} onClick={() => mutation.mutate({ file, action })}>{label}</button>)}</>}</div>
    </article>)}</div>{!query.data.items.length && <Empty />}</>}
    {evidencePath && <section className="panel"><div className="toolbar"><h2>使用证据</h2><button onClick={() => update({ evidence: null })}>关闭证据</button></div><p className="path">{evidencePath}</p>
      <ErrorMessage error={evidence.error} />{evidence.isPending && <Loading />}{evidence.data && <><p>来源状态：{evidence.data.sourceStatus} · 确认读取 {evidence.data.confirmedReads} · 推算会话 {evidence.data.estimatedSessions} · 提及 {evidence.data.mentions}</p>
        {evidence.data.warnings.map(warning => <p key={warning} className="notice">{warning}</p>)}{evidence.data.assumptions.map(assumption => <p key={assumption} className="muted">{assumption}</p>)}<pre className="evidence">{JSON.stringify(evidence.data.evidence, null, 2)}</pre></>}</section>}
  </>;
}
