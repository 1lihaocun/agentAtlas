export function bytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / 1024 / 1024).toFixed(1)} MiB`;
}
export function date(value?: number) {
  if (!value) return '未知';
  return new Date(value * 1000).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: undefined });
}
export const categories: Record<string, string> = {
  instruction: '指令', memory: '记忆', skill: '技能', reference: '参考', command: '命令',
  hook: '钩子', config: '配置', session: '会话', log: '日志', other: '其他',
};
export function basename(path: string) { return path.split('/').at(-1) || path; }
const platformPalette: Record<string, string> = { codex: '#28613f', claude: '#8a5a22', hermes: '#3f5e8a', openclaw: '#6b4f8a', shared: '#8a9187' };
export function platformColor(platform?: string) {
  if (platform && platformPalette[platform]) return platformPalette[platform];
  let hash = 0;
  for (const char of platform || '') hash = (hash * 31 + char.charCodeAt(0)) % 360;
  return `hsl(${hash},32%,38%)`;
}
