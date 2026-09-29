import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, post } from '../../shared/api/client';
import type { JobStatus, Settings } from '../../shared/api/types';
import { categories } from '../../shared/lib/format';
import { useActions } from '../../shared/lib/navigation';
import { ConfirmDialog } from '../../shared/ui/ConfirmDialog';
import { ErrorMessage, Loading } from '../../shared/ui/feedback';

export function IndexControls() {
  const [open, setOpen] = useState(false);
  const client = useQueryClient();
  const actions = useActions();
  const settings = useQuery({ queryKey: ['settings'], queryFn: () => api<Settings>('/settings'), enabled: open, staleTime: 0 });
  const jobs = useQuery({ queryKey: ['jobs'], queryFn: () => api<JobStatus>('/assets/status') });
  const index = useMutation({
    mutationFn: () => post('/search/index', { embeddings: true, confirmCloud: true, maxChunks: 256 }),
    onSuccess: () => {
      setOpen(false);
      void client.invalidateQueries({ queryKey: ['jobs'] });
      actions.notify('向量索引任务已启动');
    },
  });
  const values = settings.data;
  const ready = Boolean(values?.baseUrl && values.model && values.apiKeyConfigured && values.categories.length);
  return <>
    <button type="button" disabled={jobs.data?.job.running || index.isPending} onClick={() => { index.reset(); setOpen(true); }}>建立向量索引</button>
    <ConfirmDialog open={open} onOpenChange={setOpen} title="发送文件建立向量索引" pending={index.isPending}
      confirmDisabled={!ready || settings.isFetching || settings.isError || jobs.data?.job.running} onConfirm={() => index.mutate()}>
      {settings.isFetching ? <Loading /> : settings.isError ? <ErrorMessage error={settings.error} /> : values && <>
        <p>服务：{values.baseUrl || '未配置'}</p>
        <p>模型：{values.model || '未配置'}</p>
        <p>发送类别：{values.categories.map(category => categories[category] || category).join('、') || '未允许任何类别'}</p>
        {!ready && <p className="notice">请在项目根目录的 .env 中完整填写 embedding 服务、模型、密钥和允许发送的文件类别。</p>}
      </>}
      <p className="muted">每次至多处理 256 个待索引片段，后续执行继续处理剩余片段。确认后会将所列类别的文件内容发送给上述服务，不受当前列表筛选条件限制。</p>
      {jobs.data?.job.running && <p className="notice">当前有任务正在执行，请等待完成。</p>}
      <ErrorMessage error={index.error} />
    </ConfirmDialog>
  </>;
}
