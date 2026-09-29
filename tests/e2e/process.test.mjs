import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { trackProcess } from './process.mjs';

test('暂停的真实进程在 TERM 超时后被 KILL 并等待退出', async () => {
  const child = spawn(process.execPath, [fileURLToPath(new URL('./hold-process.mjs', import.meta.url))], { stdio: ['ignore', 'pipe', 'inherit'] });
  const lifecycle = trackProcess(child);
  try {
    await once(child.stdout, 'data');
    child.kill('SIGSTOP');
    const result = await lifecycle.stop(100);
    assert.equal(result.signal, 'SIGKILL');
    assert.equal(child.signalCode, 'SIGKILL');
  } finally { await lifecycle.stop(100); }
});

test('不存在的命令返回原始启动错误且退出回收不挂起', async () => {
  const child = spawn('/agentatlas-command-does-not-exist', [], { stdio: 'ignore' });
  const lifecycle = trackProcess(child);
  await new Promise(resolve => child.once('error', resolve));
  assert.equal(lifecycle.error.code, 'ENOENT');
  assert.equal((await lifecycle.stop(100)).error.code, 'ENOENT');
});
