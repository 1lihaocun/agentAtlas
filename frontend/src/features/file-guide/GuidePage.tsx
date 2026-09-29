import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router';
import { api, queryString } from '../../shared/api/client';
import type { Guide, GuideLibrary } from '../../shared/api/types';
import { useActions, useFilters } from '../../shared/lib/navigation';
import { ErrorMessage, Loading, PageTitle } from '../../shared/ui/feedback';
import { GuideDetail } from '../../shared/ui/GuideDetail';

export function GuidePage() {
  const { typeId } = useParams(); const { params, update } = useFilters(); const actions = useActions(); const path = params.get('path') || '';
  const library = useQuery({ queryKey: ['guides'], queryFn: () => api<GuideLibrary>('/file-guide') });
  const description = useQuery({ queryKey: ['guide', path], enabled: Boolean(path), queryFn: () => api<Guide>('/file-guide?' + queryString({ path })) });
  const provenance = useQuery({ queryKey: ['provenance', path], enabled: Boolean(path), queryFn: () => api<Record<string, unknown>>('/provenance?' + queryString({ path })) });
  const selected = path ? description.data : library.data?.types.find(type => type.id === typeId);
  const q = (params.get('q') || '').toLocaleLowerCase();
  return <><PageTitle title="文件说明" />
    <ErrorMessage error={library.error || description.error || provenance.error} />{(library.isPending || (path && description.isPending)) && <Loading />}
    {path && <p className="path">{path} <button onClick={() => actions.openFile(path)}>查看文件</button></p>}
    {!typeId && !path && <input aria-label="搜索文件说明" placeholder="搜索类型与用途" value={params.get('q') || ''} onChange={event => update({ q: event.target.value }, true)} />}
    {typeId && library.data && !selected && <p role="alert" className="error">文件类型不存在。</p>}
    {selected ? <><Link to="/file-types">全部文件类型</Link><GuideDetail guide={selected} />
      {provenance.data && <details className="panel"><summary>来源与生成过程</summary><pre className="evidence">{JSON.stringify(provenance.data, null, 2)}</pre></details>}</> :
      !path && !typeId && <div className="cards">{library.data?.types.filter(guide => (guide.label + guide.purpose + guide.id).toLocaleLowerCase().includes(q)).map(guide => <Link className="panel guide-card" key={guide.id} to={'/file-types/' + encodeURIComponent(guide.id)}><h2>{guide.label}</h2><p>{guide.purpose}</p></Link>)}</div>}
    <p className="muted">{library.data?.notice}</p>
  </>;
}
