import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router';
import { api, queryString } from '../../shared/api/client';
import type { FileContext, FileDocument } from '../../shared/api/types';
import { useActions } from '../../shared/lib/navigation';
import { bytes, categories, date } from '../../shared/lib/format';
import { ErrorMessage, Loading } from '../../shared/ui/feedback';

export function FilePage() {
  const [params] = useSearchParams();
  const path = params.get('path') || '';
  return <><h1>文件详情</h1>
    {!path && <p className="muted">通过文件列表打开文件，或者使用地址中的 path 参数定位文件。</p>}
    {path && <FileMeta path={path} key={path} />}
  </>;
}
function FileMeta({ path }: { path: string }) {
  const actions = useActions();
  const document = useQuery({ queryKey: ['file', path], queryFn: ({ signal }) => api<FileDocument>('/file?' + queryString({ path }), { signal }) });
  const context = useQuery({ queryKey: ['context', path], queryFn: ({ signal }) => api<FileContext>('/file/context?' + queryString({ path }), { signal }) });
  return <><ErrorMessage error={document.error || context.error} />
    {(document.isPending || context.isPending) && <Loading />}
    {document.data && <section className="panel"><h2>{document.data.path.split('/').pop()}</h2>
      <p className="path">{document.data.path}</p>
      <dl className="metadata">
        <div><dt>用途</dt><dd>{categories[document.data.category] || document.data.category}</dd></div>
        <div><dt>大小</dt><dd>{bytes(document.data.bytes)}</dd></div>
        <div><dt>修改时间</dt><dd>{date(document.data.mtime)}</dd></div>
        <div><dt>可编辑</dt><dd>{document.data.editable ? '是' : '否' + (document.data.readOnlyReason ? '：' + document.data.readOnlyReason : '')}</dd></div>
      </dl>
      <div className="toolbar"><button onClick={() => actions.openFile(path)}>在查看器中打开</button></div>
    </section>}
    {context.data?.memory && <section className="panel"><h2>记忆元数据</h2>
      <dl className="metadata">
        <div><dt>标题</dt><dd>{context.data.memory.memoryInfo?.title || context.data.memory.name}</dd></div>
        <div><dt>说明</dt><dd>{context.data.memory.memoryInfo?.description || '未提供'}</dd></div>
        <div><dt>作用层级</dt><dd>{context.data.memory.semantics.label}</dd></div>
        <div><dt>加载方式</dt><dd>{context.data.memory.semantics.loading.label}</dd></div>
      </dl>
    </section>}
  </>;
}
