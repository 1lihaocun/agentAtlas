import { useEffect, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, post } from '../../shared/api/client';
import type { JobStatus } from '../../shared/api/types';
import { ErrorMessage } from '../../shared/ui/feedback';

export function JobControls() {
  const client = useQueryClient(); const version = useRef('');
  const query = useQuery({ queryKey: ['jobs'], queryFn: () => api<JobStatus>('/assets/status'), refetchInterval: 1500 });
  useEffect(() => {
    const next = query.data?.catalogVersion;
    if (next && next !== version.current) {
      version.current = next;
      for (const key of ['assets', 'instructions', 'memories', 'reviews', 'duplicates', 'search', 'sections', 'file', 'context']) void client.invalidateQueries({ queryKey: [key] });
    }
  }, [query.data?.catalogVersion, client]);
  const scan = useMutation({ mutationFn: () => post('/rescan', {}), onSuccess: () => void client.invalidateQueries({ queryKey: ['jobs'] }) });
  const job = query.data?.job;
  const stages: Record<string, string> = { queued: '排队中', instructions: '正在扫描指令文件', discover: '正在发现文件', keyword: '正在更新关键词索引', embedding: '正在建立向量索引', done: '扫描完成', idle: '就绪', error: '上次扫描失败' };
  const progress = job?.running ? (job.kind === 'index' ? '索引中：' : '扫描中：') + (stages[job.stage] || job.stage) : '';
  return <div className="job-controls"><button disabled={job?.running || scan.isPending} onClick={() => scan.mutate()}>重新扫描</button>
    <span className="muted">{progress}</span>
    <ErrorMessage error={query.error || scan.error || (job?.error ? new Error(job.error) : null)} /></div>;
}
