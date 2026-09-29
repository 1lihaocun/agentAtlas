import type { Guide } from '../api/types';

export function GuideDetail({ guide }: { guide: Guide }) {
  return <section className="panel"><h2>{guide.label}</h2><p>{guide.purpose}</p><dl className="metadata">{[['角色', guide.role], ['生成者', guide.producer], ['读取者', guide.consumer], ['加载方式', guide.loadingMechanism], ['生成方式', guide.generationMechanism], ['证据', guide.evidence]].map(([label, value]) => value && <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    {guide.filename?.fields?.map(field => <p key={field.key}><b>{field.label}</b> {field.value} — {field.meaning}</p>)}
    {guide.cautions?.map(text => <p className="notice" key={text}>{text}</p>)}
    {guide.sources?.map((source, index) => <p key={index}>{source.url ? <a href={source.url} target="_blank" rel="noreferrer">{source.label || source.url}</a> : <span>{source.path}</span>}</p>)}
  </section>;
}
