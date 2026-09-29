import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

export async function indexControlChecks({ page, url, work, checks, service }) {
  const requests = [];
  const record = request => requests.push({ url: request.url(), method: request.method() });
  page.on('request', record);
  const envPath = path.join(work, '.env');
  try {
    await page.goto(url + '/settings');
    await page.waitForURL('**/assets');
    assert.equal(await page.getByRole('link', { name: '索引设置', exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: '保存设置', exact: true }).count(), 0);
    const button = page.getByRole('button', { name: '建立向量索引', exact: true });
    await button.waitFor();
    await page.locator('tbody tr').first().waitFor();
    await page.screenshot({ path: path.join(work, 'index-toolbar.png') });
    await button.click();
    const dialog = page.getByRole('dialog');
    await dialog.getByText(/请在项目根目录的 \.env/).waitFor();
    assert.equal(await dialog.getByRole('button', { name: '确认执行', exact: true }).isDisabled(), true);
    await dialog.getByRole('button', { name: '取消', exact: true }).click();
    checks.push('删除索引设置入口与表单，旧地址跳转到检索页，缺少 .env 配置时禁止建索引');

    const config = '# Manual configuration\nATLAS_TRANSLATION_MODEL=keep-fixture\n'
      + `ATLAS_EMBEDDING_BASE_URL=${url}/embedding-fixture\n`
      + 'ATLAS_EMBEDDING_MODEL=embedding-fixture\nATLAS_EMBEDDING_API_KEY=e2e-fixture-secret\n'
      + 'ATLAS_EMBEDDING_CATEGORIES=instruction,memory\n';
    await writeFile(envPath, config);
    await button.click();
    await dialog.getByText('模型：embedding-fixture', { exact: true }).waitFor();
    assert.match(await dialog.textContent(), /发送类别：指令、记忆/);
    assert(!(await dialog.textContent()).includes('e2e-fixture-secret'));
    assert.equal(await dialog.locator('input').count(), 0);
    assert.equal(await dialog.getByRole('button', { name: '确认执行', exact: true }).isDisabled(), false);
    await page.screenshot({ path: path.join(work, 'index-confirmation.png') });
    await dialog.getByRole('button', { name: '取消', exact: true }).click();
    assert(!requests.some(request => request.method === 'POST' && request.url.endsWith('/api/search/index')));
    assert.equal(await readFile(envPath, 'utf8'), config);

    // A real job with an empty permitted category makes no model requests.
    const emptyScope = config.replace('embedding-fixture\nATLAS_EMBEDDING_API_KEY', 'updated-fixture\nATLAS_EMBEDDING_API_KEY')
      .replace('CATEGORIES=instruction,memory', 'CATEGORIES=session');
    await writeFile(envPath, emptyScope);
    service.kill('SIGSTOP');
    try {
      await button.click();
      await dialog.getByRole('status').waitFor();
      assert.equal(await dialog.getByRole('button', { name: '确认执行', exact: true }).isDisabled(), true);
      assert.equal(await dialog.getByRole('button', { name: '取消', exact: true }).isDisabled(), false);
    } finally { service.kill('SIGCONT'); }
    await dialog.getByText('模型：updated-fixture', { exact: true }).waitFor();
    assert.match(await dialog.textContent(), /发送类别：会话/);
    const accepted = page.waitForResponse(response => response.url().endsWith('/api/search/index') && response.request().method() === 'POST');
    await dialog.getByRole('button', { name: '确认执行', exact: true }).click();
    const response = await accepted;
    assert.equal(response.status(), 202);
    assert.deepEqual(response.request().postDataJSON(), { embeddings: true, confirmCloud: true, maxChunks: 256 });
    await dialog.waitFor({ state: 'hidden' });
    await page.waitForFunction(async () => {
      const response = await fetch('/api/assets/status');
      const { data } = await response.json();
      return !data.job.running;
    });
    const status = await (await page.request.get(url + '/api/assets/status')).json();
    assert.equal(status.data.job.error, null);
    assert.equal(await readFile(envPath, 'utf8'), emptyScope);
    assert(!requests.some(request => request.method === 'POST' && request.url.endsWith('/api/settings')));
    checks.push('检索区只读展示最新模型和发送范围，读取中禁用确认，取消不发送，确认后启动真实空范围索引任务');
  } finally {
    page.off('request', record);
  }
}
