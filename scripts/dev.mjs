import { spawn } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const work = path.join(root, '.agentatlas/work');
await mkdir(work, { recursive: true });
const backendPort = Number(process.env.ATLAS_PORT || 7788);
const frontendPort = Number(process.env.ATLAS_FRONTEND_PORT || 5173);
for (const [name, port] of [['ATLAS_PORT', backendPort], ['ATLAS_FRONTEND_PORT', frontendPort]]) {
  if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error(`${name} 必须为 1024 到 65535 之间的整数`);
}
const workspace = path.resolve(process.env.ATLAS_WORKSPACE || root);
const environment = { ...process.env, TMPDIR: work, ATLAS_ALLOWED_ORIGINS: JSON.stringify([`http://127.0.0.1:${frontendPort}`]) };
const backend = spawn(path.join(root, 'backend/.venv/bin/atlas'), ['--workspace', workspace, 'serve', '--port', String(backendPort)], { cwd: root, env: environment, stdio: 'inherit' });
const frontend = spawn(process.execPath, [path.join(root, 'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(frontendPort)], { cwd: path.join(root, 'frontend'), env: { ...environment, ATLAS_API_URL: `http://127.0.0.1:${backendPort}` }, stdio: 'inherit' });
console.log(`AgentAtlas 前端访问地址: http://127.0.0.1:${frontendPort}（开发模式，前端热更新）`);
console.log(`后端 API 地址: http://127.0.0.1:${backendPort}`);
console.log('按 Ctrl-C 停止服务');
let closing = false;
function stop(code) {
  if (closing) return;
  closing = true;
  process.exitCode = code;
  backend.kill('SIGTERM'); frontend.kill('SIGTERM');
}
for (const child of [backend, frontend]) {
  child.on('error', error => { console.error(error); stop(1); });
  child.on('exit', code => stop(code ?? 1));
}
process.on('SIGINT', () => stop(0));
process.on('SIGTERM', () => stop(0));
