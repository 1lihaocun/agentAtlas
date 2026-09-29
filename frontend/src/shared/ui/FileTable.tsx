import type { Asset } from '../api/types';
import { bytes, categories, date, platformColor } from '../lib/format';
import { Empty } from './feedback';

export function FileTable({ files, openFile, openGuide }: { files: Asset[]; openFile: (path: string) => void; openGuide: (path: string) => void }) {
  if (!files.length) return <Empty />;
  return <div className="table-scroll"><table><thead><tr><th>文件 / 路径</th><th>大小</th><th>修改时间</th><th>文件说明</th></tr></thead>
    <tbody>{files.map(file => <tr key={file.path}>
      <td><button className="file-link" onClick={() => openFile(file.path)} disabled={file.searchable === false || file.restricted} title={file.reason}>
        <span className="file-top"><strong>{file.name}</strong>
          <span className="tag platform"><span className="dot" style={{ background: platformColor(file.platform) }} />{file.platform || file.kind || '共享'}</span>
          <span className="tag">{categories[file.category || 'instruction']}</span>
          {file.worktree && <span className="tag">worktree</span>}{file.dup && <span className="tag warning">重复</span>}
          {file.editable === false && <span className="tag outline">只读</span>}</span>
        <span className="path">{file.path}</span></button></td>
      <td className="nowrap">{bytes(file.bytes)}</td><td className="muted nowrap">{date(file.mtime)}</td>
      <td><button className="guide-link" onClick={() => openGuide(file.path)}>说明</button></td>
    </tr>)}</tbody></table></div>;
}
