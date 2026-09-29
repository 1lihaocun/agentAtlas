import assert from 'node:assert/strict';
import path from 'node:path';
import { readFile, writeFile, unlink } from 'node:fs/promises';

export async function reviewChecks({ page, url, project, checks, service }) {
  const file = path.join(project, 'feature-37/AGENTS.md');
  await page.goto(url + '/assets?q=Searchtoken37');
  const hit = page.locator('.result-list article').first();
  await hit.getByRole('button', { name: /在查看器中定位/ }).click();
  await page.getByLabel('文件查看器', { exact: true }).waitFor();
  assert.equal(new URL(page.url()).searchParams.get('file'), file);
  const editor = page.getByLabel('文件内容', { exact: true });
  await editor.waitFor();
  await page.waitForFunction(() => document.querySelector('textarea[aria-label="文件内容"]')?.readOnly === false);
  await editor.evaluate(input => { input.focus(); input.setSelectionRange(input.value.length, input.value.length); input.dispatchEvent(new Event('select', { bubbles: true })); });
  await editor.pressSequentially(' abc', { delay: 50 });
  assert.match(await editor.inputValue(), /Keep this section\.\n abc$/);
  await page.getByRole('button', { name: '放弃修改', exact: true }).click();
  await page.getByRole('button', { name: '确认执行', exact: true }).click();
  await page.getByRole('button', { name: '关闭文件查看器', exact: true }).click();
  assert.equal(new URL(page.url()).searchParams.get('line'), null);
  checks.push('检索命中原子导航、深链编辑光标稳定与关闭清理行号');

  await page.goto(url + '/files?' + new URLSearchParams({ path: file, panel: 'sections' }));
  await page.getByRole('button', { name: /^选择章节：/ }).click();
  await page.getByRole('menuitem', { name: 'Details', exact: true }).click();
  const section = page.getByLabel('章节内容', { exact: true });
  await page.waitForFunction(() => document.querySelector('textarea[aria-label="章节内容"]')?.value === '## Details\nKeep this section.\n');
  await section.fill('## Details\nUpdated second section.\n');
  await page.getByRole('link', { name: '记忆', exact: true }).click();
  await page.locator('.draft-list button').click();
  await section.waitFor();
  assert.equal(await section.inputValue(), '## Details\nUpdated second section.\n');
  const savedSection = new URL(page.url()).searchParams.get('section');
  await page.getByRole('button', { name: '保存章节', exact: true }).click();
  await page.waitForFunction(() => !document.querySelector('.draft-list'));
  await page.waitForFunction(previous => new URL(location.href).searchParams.get('section') !== previous, savedSection);
  assert.equal(await section.inputValue(), '## Details\nUpdated second section.\n');
  assert.equal(await readFile(file, 'utf8'), '# Feature 37\nSearchtoken37 original content.\n## Details\nUpdated second section.\n');
  checks.push('章节草稿恢复到对应面板、保存非首章节后保持选择');

  await page.goto(url + '/assets?q=Searchtoken52');
  const missing = path.join(project, 'feature-52/AGENTS.md');
  const original = await readFile(missing, 'utf8');
  const expand = page.locator('.result-list article').first().getByRole('button', { name: /展开整块/ });
  await expand.waitFor();
  await unlink(missing);
  try {
    await expand.click();
    await page.locator('.search-area [role="alert"]').waitFor();
  } finally { await writeFile(missing, original); }
  checks.push('源文件删除后展开片段显示真实错误');

  await page.goto(url + '/duplicates');
  await page.getByRole('button', { name: '以此文件同步', exact: true }).first().click();
  const dialog = page.getByRole('dialog');
  await dialog.waitFor();
  const source = path.join(project, 'AGENTS.md');
  const sourceText = await readFile(source, 'utf8');
  await writeFile(source, sourceText + 'External change.\n');
  try {
    // 暂停真实服务，稳定覆盖等待响应期间的交互，不拦截或替换请求。
    service.kill('SIGSTOP');
    try {
      await dialog.getByRole('button', { name: '确认执行', exact: true }).click();
      await dialog.getByRole('button', { name: '正在执行…', exact: true }).waitFor();
      await page.keyboard.press('Escape');
      assert.equal(await dialog.isVisible(), true, '操作进行中不能通过 Escape 关闭确认框');
      await page.locator('.dialog-overlay').click({ position: { x: 5, y: 5 }, force: true });
      assert.equal(await dialog.isVisible(), true, '操作进行中不能通过遮罩关闭确认框');
    } finally { service.kill('SIGCONT'); }
    await dialog.getByRole('alert').waitFor();
    assert.match(await dialog.innerText(), /变化|更新|冲突/);
    await dialog.getByRole('button', { name: '取消', exact: true }).click();
  } finally { await writeFile(source, sourceText); }
  checks.push('等待真实服务时禁止 Escape/遮罩关闭、同步版本冲突在框内可见');

  await page.goto(url + '/files?' + new URLSearchParams({ path: file, panel: 'translation' }));
  await page.getByRole('button', { name: '翻译为中文', exact: true }).click();
  await dialog.waitFor();
  const latest = await readFile(file, 'utf8');
  await writeFile(file, latest + 'Changed before translation.\n');
  try {
    await dialog.getByRole('button', { name: '确认执行', exact: true }).click();
    await dialog.getByRole('alert').waitFor();
    await dialog.getByRole('button', { name: '取消', exact: true }).click();
  } finally { await writeFile(file, latest); }
  checks.push('翻译真实版本冲突在确认框内可见、未调用模型');

  await page.goto(url + '/assets?q=Htmlprobe');
  await page.locator('.result-list article').first().waitFor();
  assert.equal(await page.locator('.result-list img, .result-list script').count(), 0);
  assert.equal(await page.evaluate(() => window.atlasXss), undefined);
  assert.match(await page.locator('.result-list pre').first().innerText(), /<img src=x/);
  assert.equal(await page.locator('.result-list mark').first().innerText(), 'Htmlprobe');
  checks.push('真实索引 HTML 内容只显示文本且保留命中高亮');

  await page.goto(url + '/assets?q=Chunkprobe');
  await page.locator('.result-list article').first().getByRole('button', { name: /展开整块/ }).click();
  const chunk = page.locator('.chunk-full').first();
  await chunk.waitFor();
  assert.match(await chunk.innerText(), /Chunkprobe complete line\./);
  checks.push('截断文件中已完整读取的高行号片段正确展开');
}
