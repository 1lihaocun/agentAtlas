import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { setTimeout as delay } from 'node:timers/promises';
import path from 'node:path';
import { trackProcess } from './process.mjs';

export async function proxyChecks({ browser, repository, work, checks }) {
  const listener = createServer();
  await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
  const port = listener.address().port;
  await new Promise(resolve => listener.close(resolve));
  const child = spawn(process.execPath, [path.join(repository, 'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
    cwd: path.join(repository, 'frontend'), env: { ...process.env, TMPDIR: work, ATLAS_API_URL: 'http://127.0.0.1:1' }, stdio: 'ignore',
  });
  const lifecycle = trackProcess(child);
  const page = await browser.newPage();
  try {
    let ready = false;
    for (let attempt = 0; attempt < 100; attempt++) {
      if (lifecycle.error) throw lifecycle.error;
      if (child.exitCode !== null) throw new Error('Vite 提前退出');
      try { ready = (await fetch(`http://127.0.0.1:${port}`)).ok; }
      catch (error) { if (error.cause?.code !== 'ECONNREFUSED') throw error; }
      if (ready) break;
      await delay(100);
    }
    assert(ready, '真实 Vite 代理必须启动');
    await page.goto(`http://127.0.0.1:${port}/assets`);
    const alert = page.locator('main [role="alert"]');
    await alert.waitFor();
    assert.match(await alert.innerText(), /500/);
    assert.doesNotMatch(await alert.innerText(), /JSON|Unexpected|SyntaxError/);
    checks.push('真实 Vite 上游断连时保留 HTTP 状态并显示可读错误');
  } finally { try { await page.close(); } finally { await lifecycle.stop(); } }
}
