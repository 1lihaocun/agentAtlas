import { useState } from 'react';
import { ChevronRight } from 'lucide-react';
import type { Asset } from '../api/types';
import { bytes, date } from '../lib/format';
import { Empty } from './feedback';

interface TreeNode { name: string; path: string; children: Map<string, TreeNode>; files: Asset[] }

// Finder 风格的可展开目录树：目录行与文件行混排，目录行可展开显示下级，缩进表达层级。
export function DirectoryTree({ files, openFile, openGuide }: {
  files: Asset[]; openFile: (path: string) => void; openGuide: (path: string) => void;
}) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  if (!files.length) return <Empty />;
  // 以用户主目录为根逐层构建真实目录树；只保留“通向文件的链上的目录”，同级同名目录天然是不同节点（路径不同）。
  const home = '/Users/' + (files[0]?.path.split('/')[2] ?? '');
  const root: TreeNode = { name: home.split('/').pop() || '', path: home, children: new Map(), files: [] };
  for (const file of files) {
    const parts = file.path.split('/');
    let node = root;
    for (let depth = 3; depth < parts.length - 1; depth++) {
      const path = parts.slice(0, depth + 1).join('/');
      let child = node.children.get(parts[depth]);
      if (!child) { child = { name: parts[depth], path, children: new Map(), files: [] }; node.children.set(parts[depth], child); }
      node = child;
    }
    node.files.push(file);
  }
  const sortNodes = (nodes: Map<string, TreeNode>) => [...nodes.values()].sort((a, b) => a.name.localeCompare(b.name));
  function toggle(path: string) { setExpanded(current => ({ ...current, [path]: !current[path] })); }
  function renderNode(node: TreeNode, depth: number): React.ReactNode[] {
    const rows: React.ReactNode[] = [];
    for (const child of sortNodes(node.children)) {
      const open = !!expanded[child.path];
      const count = countFiles(child);
      rows.push(<tr key={child.path} className="tree-folder-row">
        <td><button className="tree-name" onClick={() => toggle(child.path)} aria-expanded={open} style={{ paddingLeft: 14 + depth * 18 }}>
          <ChevronRight size={13} className="tree-chevron" aria-hidden="true" style={{ transform: open ? 'rotate(90deg)' : undefined }} />
          <span className="tree-title">{child.name}</span>
          <span className="muted tree-count">{count}</span></button></td>
        <td /><td /><td /></tr>);
      if (open) rows.push(...renderNode(child, depth + 1));
    }
    for (const file of [...node.files].sort((a, b) => a.name.localeCompare(b.name))) {
      rows.push(<tr key={file.path} className="tree-file-row">
        <td><button className="file-link tree-file" onClick={() => openFile(file.path)} disabled={file.searchable === false || file.restricted} title={file.path} style={{ paddingLeft: 14 + depth * 18 }}>
          <strong>{file.name}</strong>
          {file.dup && <span className="tag warning">重复</span>}
          {file.editable === false && <span className="tag outline">只读</span>}</button></td>
        <td className="nowrap">{bytes(file.bytes)}</td><td className="muted nowrap">{date(file.mtime)}</td>
        <td><button className="guide-link" onClick={() => openGuide(file.path)}>说明</button></td></tr>);
    }
    return rows;
  }
  return <div className="table-scroll"><table className="tree-table"><thead><tr><th>名称</th><th>大小</th><th>修改时间</th><th>文件说明</th></tr></thead>
    <tbody>{renderNode(root, 0)}</tbody></table></div>;
}

function countFiles(node: TreeNode): number {
  let total = node.files.length;
  for (const child of node.children.values()) total += countFiles(child);
  return total;
}
