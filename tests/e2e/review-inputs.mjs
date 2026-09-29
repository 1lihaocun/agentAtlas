import assert from 'node:assert/strict';

export async function reviewInputChecks({ page, url, checks, service, project }) {
  const failures = [];
  async function check(name, action) {
    try { await action(); checks.push(name); }
    catch (error) { failures.push(new Error(name, { cause: error })); }
  }
  await check('审阅非法天数明确报错且不请求非法值', async () => {
    const requests = [];
    const record = request => { if (request.url().includes('/api/retirement?')) requests.push(request.url()); };
    page.on('request', record);
    try {
      await page.goto(url + '/reviews?days=abc');
      await page.getByRole('alert').waitFor();
      assert.match(await page.getByRole('alert').innerText(), /天数.*无效/);
      assert.deepEqual(requests, []);
    } finally { page.off('request', record); }
  });
  await check('单段指令路径返回目录根而非截断名称', async () => {
    await page.goto(url + '/instructions/directory?path=foo');
    const parent = page.getByRole('link', { name: '上级目录', exact: true });
    await parent.waitFor();
    assert.equal(new URL(await parent.getAttribute('href'), url).searchParams.get('path'), null);
  });

  await check('记忆输入合并连续击键请求并保存最终 URL', async () => {
    await page.goto(url + '/memories/scope');
    const input = page.getByLabel('搜索记忆元数据', { exact: true });
    await input.waitFor();
    const requests = [];
    const record = request => { if (request.url().includes('/api/memories?') && new URL(request.url()).searchParams.get('q')) requests.push(request.url()); };
    page.on('request', record);
    try {
      await input.pressSequentially('project', { delay: 20 });
      await page.waitForFunction(() => new URL(location.href).searchParams.get('q') === 'project');
      await page.waitForFunction(() => !document.querySelector('main [role="status"]'));
      assert.equal(requests.length, 1);
    } finally { page.off('request', record); }
  });
  await check('按路径查看文件说明时不闪现类型卡片列表', async () => {
    await page.goto(url + '/file-types');
    await page.locator('.guide-card').first().waitFor();
    await page.getByRole('link', { name: '全部文件', exact: true }).click();
    await page.locator('tbody tr').first().waitFor();
    service.kill('SIGSTOP');
    try {
      await page.getByRole('button', { name: '说明', exact: true }).first().click();
      await page.waitForURL('**/file-types?path=**');
      await page.getByRole('heading', { name: '文件说明', exact: true }).waitFor();
      assert.equal(await page.locator('.guide-card').count(), 0);
      assert(await page.locator('main [role="status"]').count() > 0);
    } finally { service.kill('SIGCONT'); }
  });
  await check('审阅页面显示真实证据来源状态', async () => {
    const response = page.waitForResponse(response => response.url().includes('/api/retirement?'));
    await page.goto(url + '/reviews');
    const { data } = await (await response).json();
    await page.waitForFunction(() => !document.querySelector('main [role="status"]'));
    assert.match(await page.locator('main').innerText(), new RegExp('来源状态：' + data.sourceStatus));
    for (const warning of data.warnings) assert((await page.locator('main').innerText()).includes(warning));
  });
  await check('过大指令页码首帧使用钳制后的数据窗口', async () => {
    await page.addInitScript(() => {
      window.atlasBlankInstructionPage = false;
      new MutationObserver(() => {
        if (location.pathname !== '/instructions/directory') return;
        const total = Number(document.querySelector('main .stats b')?.textContent);
        if (total > 0 && document.querySelector('main .folder-list') && !document.querySelector('main .folder-list > *, main tbody tr')) window.atlasBlankInstructionPage = true;
      }).observe(document, { childList: true, subtree: true });
    });
    await page.goto(url + '/instructions/directory?' + new URLSearchParams({ path: project, page: '999' }));
    await page.waitForFunction(() => new URL(location.href).searchParams.get('page') !== '999' && document.querySelector('main tbody tr'));
    assert.equal(await page.evaluate(() => window.atlasBlankInstructionPage), false);
  });
  await check('真实章节加载未观察到未初始化可编辑窗口', async () => {
    await page.addInitScript(() => {
      window.atlasUninitializedEditor = false;
      new MutationObserver(() => {
        const editor = document.querySelector('textarea[aria-label="章节内容"]');
        if (editor && editor.value === '' && !editor.readOnly) window.atlasUninitializedEditor = true;
      }).observe(document, { childList: true, subtree: true, attributes: true });
    });
    await page.goto(url + '/files?' + new URLSearchParams({ path: project + '/feature-38/AGENTS.md', panel: 'sections' }));
    await page.waitForFunction(() => document.querySelector('textarea[aria-label="章节内容"]')?.value.includes('Feature 38'));
    assert.equal(await page.evaluate(() => window.atlasUninitializedEditor), false);
  });
  if (failures.length) throw new AggregateError(failures, '前端输入与加载回归失败');
}
