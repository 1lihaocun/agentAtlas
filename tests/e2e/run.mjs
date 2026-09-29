import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createWriteStream } from 'node:fs';
import { createServer } from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '../../frontend/node_modules/playwright/index.mjs';
import { reviewChecks } from './review.mjs';
import { reviewInputChecks } from './review-inputs.mjs';
import { indexControlChecks } from './index-controls.mjs';
import { proxyChecks } from './proxy.mjs';
import { trackProcess } from './process.mjs';

const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const work = path.join(repository, '.agentatlas/work', `browser-${Date.now()}`);
const home = path.join(work, 'home'), project = path.join(home, 'Code/project'), memory = path.join(home, '.codex/memories');
await mkdir(path.join(project, '.git'), { recursive: true });
await mkdir(path.join(memory, 'extensions/custom/notes'), { recursive: true });
await mkdir(path.join(home, '.codex/sessions'), { recursive: true });
for (let index = 0; index < 56; index++) {
  const directory = path.join(project, `feature-${String(index).padStart(2, '0')}`);
  await mkdir(directory);
  await writeFile(path.join(directory, 'AGENTS.md'), `# Feature ${index}\nSearchtoken${index} original content.\n## Details\nKeep this section.\n`);
}
await writeFile(path.join(project, 'AGENTS.md'), '# Shared instructions\nProject guidance.\n');
await writeFile(path.join(project, 'CLAUDE.md'), '# Shared instructions\nProject guidance.\n');
await writeFile(path.join(project, 'feature-54/AGENTS.md'), 'a\n'.repeat(100000) + 'Chunkprobe complete line.\n' + 'b\n'.repeat(1000000));
await writeFile(path.join(project, 'feature-55/AGENTS.md'), '# Htmlprobe\n<img src=x onerror="window.atlasXss=true"> & <script>window.atlasXss=true</script>\n');
await writeFile(path.join(memory, 'MEMORY.md'), '---\ntitle: 项目记忆\ndescription: 目录与项目说明\n---\n# Memory\nRead the project rules.\n');
await writeFile(path.join(memory, 'extensions/custom/notes/day.md'), '# 日常记录\nRelated project: ' + project + '\n');
const listener = createServer();
await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
const port = listener.address().port;
await new Promise(resolve => listener.close(resolve));
const url = `http://127.0.0.1:${port}`;
const log = createWriteStream(path.join(work, 'server.log'));
const service = spawn(path.join(repository, 'backend/.venv/bin/atlas'), ['--workspace', work, 'serve', '--port', String(port), '--frontend-dir', path.join(repository, 'frontend/dist')], {
  cwd: repository, env: { ...process.env, TMPDIR: work, ATLAS_HOME: home, ATLAS_SCAN_ROOTS: JSON.stringify([project]) }, stdio: ['ignore', 'pipe', 'pipe'],
});
service.stdout.pipe(log); service.stderr.pipe(log);
const lifecycle = trackProcess(service);
const errors = [], requests = [], checks = [];
let browser;
try {
  let ready = false;
  for (let attempt = 0; attempt < 150; attempt++) {
    if (lifecycle.error) throw new Error('无法启动后端，请先执行 make install', { cause: lifecycle.error });
    if (service.exitCode !== null) throw new Error('后端提前退出：' + await readFile(path.join(work, 'server.log'), 'utf8'));
    const response = await fetch(url + '/api/assets/status').catch(error => {
      if (error.cause?.code !== 'ECONNREFUSED') throw error;
      return null;
    });
    if (response?.ok) { const { data } = await response.json(); if (!data.job.running) { assert.equal(data.job.error, null); ready = true; break; } }
    await delay(100);
  }
  assert(ready, '后端必须完成真实扫描');
  browser = await chromium.launch({ executablePath: process.env.ATLAS_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true,
    env: { ...process.env, TMPDIR: work } });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => requests.push({ url: request.url(), method: request.method() }));
  page.on('dialog', dialog => dialog.accept());
  await reviewChecks({ page, url, project, checks, service });
  await reviewInputChecks({ page, url, checks, service, project });
  await proxyChecks({ browser, repository, work, checks });
  async function settled() {
    await page.locator('main h1').waitFor();
    await page.waitForFunction(() => !document.querySelector('main [role="status"]'));
    assert.equal(await page.locator('[role="alert"]').count(), 0, await page.locator('body').innerText());
    assert.equal(await page.locator('select').count(), 0);
  }
  async function open(route) { await page.goto(url + route); await settled(); }
  await open('/assets?pageSize=25');
  assert.equal(await page.locator('tbody tr').count(), 25);
  await page.getByRole('button', { name: '下一页', exact: true }).click();
  await page.waitForURL('**page=2');
  await settled();
  await page.waitForFunction(() => document.querySelectorAll('tbody tr').length === 25);
  assert.equal(await page.locator('tbody tr').count(), 25);
  await page.reload(); await settled();
  assert.equal(new URL(page.url()).searchParams.get('page'), '2');
  await page.goBack();
  await page.waitForFunction(() => new URL(location.href).searchParams.get('page') !== '2');
  await settled();
  await page.goForward();
  await page.waitForFunction(() => new URL(location.href).searchParams.get('page') === '2');
  await settled();
  checks.push('服务端分页、地址恢复与页面刷新');

  await page.getByRole('button', { name: '项目：全部', exact: true }).click();
  await page.getByLabel('搜索项目', { exact: true }).fill('Code/project');
  await page.getByRole('menuitem', { name: project, exact: true }).click();
  await page.waitForFunction(expected => new URL(location.href).searchParams.get('project') === expected, project);
  await settled();
  assert.equal(new URL(page.url()).searchParams.get('page'), '1');
  const projectTrigger = page.getByRole('button', { name: `项目：${project}`, exact: true });
  assert.equal(await projectTrigger.getAttribute('aria-expanded'), 'false');
  await page.reload(); await settled();
  await projectTrigger.click();
  assert.equal(await page.getByLabel('搜索项目', { exact: true }).inputValue(), '');
  assert.match(await page.getByRole('menuitem', { name: project, exact: true }).getAttribute('class'), /selected/);
  await page.getByLabel('搜索项目', { exact: true }).press('Escape');
  assert.equal(await projectTrigger.getAttribute('aria-expanded'), 'false');
  assert(await projectTrigger.evaluate(element => element === document.activeElement));
  await page.goBack(); await settled();
  await page.getByRole('button', { name: '项目：全部', exact: true }).waitFor();
  assert.equal(new URL(page.url()).searchParams.get('page'), '2');
  await page.getByRole('button', { name: '平台：全部', exact: true }).click();
  await page.getByRole('menuitem', { name: 'codex', exact: true }).click();
  await settled();
  assert.equal(new URL(page.url()).searchParams.get('platform'), 'codex');
  assert.equal(new URL(page.url()).searchParams.get('page'), '1');
  await page.waitForFunction(() => {
    const cells = [...document.querySelectorAll('tbody tr td:first-child .tag.platform')];
    return cells.length > 0 && cells.every(cell => cell.textContent === 'codex');
  });
  assert((await page.locator('tbody tr td:first-child .tag.platform').allTextContents()).every(text => text === 'codex'));
  await page.getByRole('button', { name: '清除筛选', exact: true }).click(); await settled();
  await page.getByRole('button', { name: '用途：全部', exact: true }).click();
  await page.getByRole('menuitem', { name: '指令', exact: true }).click();
  await settled();
  await page.waitForFunction(() => {
    const cells = [...document.querySelectorAll('tbody tr td:first-child .file-top .tag')];
    return cells.length > 0 && cells.filter(cell => !cell.classList.contains('platform')).every(cell => ['指令', '重复', 'worktree', '只读'].includes(cell.textContent));
  });
  assert((await page.locator('tbody tr td:first-child .file-top .tag:not(.platform)').allTextContents()).every(text => ['指令', '重复', 'worktree', '只读'].includes(text)));
  await page.getByRole('button', { name: '排序：文件路径', exact: true }).focus();
  await page.keyboard.press('ArrowDown');
  await page.getByRole('menu', { name: '排序选项', exact: true }).waitFor();
  await page.getByRole('menuitem', { name: '最近修改', exact: true }).focus();
  await page.keyboard.press('Enter');
  await settled();
  assert.equal(new URL(page.url()).searchParams.get('sort'), 'modified');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '项目：全部', exact: true }).click();
  const popup = await page.locator('.filter-popup').filter({ has: page.getByRole('menu', { name: '项目选项', exact: true }) }).boundingBox();
  assert(popup && popup.x >= 0 && popup.x + popup.width <= 391, JSON.stringify({ popup, dropdown: await page.locator('.filter-dropdown').filter({ has: page.getByRole('menu', { name: '项目选项', exact: true }) }).boundingBox() }));
  await page.getByLabel('搜索项目', { exact: true }).press('Escape');
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole('button', { name: '清除筛选', exact: true }).click(); await settled();
  assert.equal(new URL(page.url()).searchParams.get('platform'), null);
  assert.equal(new URL(page.url()).searchParams.get('category'), null);
  checks.push('Ant Design 下拉筛选、项目搜索、地址恢复、键盘操作与窄屏菜单');

  await page.getByRole('button', { name: '每页数量：25 条 / 页', exact: true }).click();
  await page.getByRole('menuitem', { name: '50 条 / 页', exact: true }).click();
  await settled();
  assert.equal(new URL(page.url()).searchParams.get('pageSize'), '50');
  await page.waitForFunction(() => document.querySelectorAll('tbody tr').length === 50);
  assert.equal(await page.locator('tbody tr').count(), 50);

  await page.getByLabel('搜索文件正文').fill('Searchtoken37');
  await page.getByRole('button', { name: '搜索', exact: true }).click();
  await page.locator('.result-list article').first().waitFor();
  assert.match(await page.locator('.result-list').innerText(), /feature-37/);
  await page.getByRole('button', { name: '清除搜索', exact: true }).click();
  await settled();
  checks.push('本地全文检索与清除条件');

  const editPath = path.join(project, 'feature-00/AGENTS.md');
  await open('/files?' + new URLSearchParams({ path: editPath }));
  const editor = page.getByLabel('文件内容', { exact: true });
  await editor.waitFor();
  await page.waitForFunction(() => document.querySelector('textarea')?.value.includes('original content'));
  await editor.fill('# Feature 0\nBrowser saved content.\n## Details\nKeep this section.\n');
  await page.getByRole('link', { name: '记忆', exact: true }).click(); await settled();
  await page.locator('.draft-list button').click();
  await editor.waitFor();
  assert.match(await editor.inputValue(), /Browser saved content/);
  await page.getByRole('button', { name: '保存文件', exact: true }).click();
  await page.waitForFunction(() => !document.querySelector('.draft-list'));
  assert.match(await readFile(editPath, 'utf8'), /Browser saved content/);
  await writeFile(editPath, '# External source\nChanged outside browser.\n');
  await editor.fill('# Feature 0\nBrowser resolves conflict.\n## Details\nKeep this section.\n');
  await page.getByRole('button', { name: '保存文件', exact: true }).click();
  await page.getByRole('button', { name: '以最新版本作为保存依据', exact: true }).waitFor();
  assert.match(await editor.inputValue(), /Browser resolves conflict/);
  assert.match(await readFile(editPath, 'utf8'), /External source/);
  await page.getByRole('button', { name: '以最新版本作为保存依据', exact: true }).click();
  await page.getByRole('button', { name: '确认执行', exact: true }).click();
  await page.getByRole('button', { name: '保存文件', exact: true }).click();
  await page.waitForFunction(() => !document.querySelector('.draft-list'));
  assert.match(await readFile(editPath, 'utf8'), /Browser resolves conflict/);
  await page.getByRole('button', { name: '章节编辑', exact: true }).click();
  await page.getByLabel('章节内容').waitFor();
  await page.getByRole('button', { name: /^选择章节：/ }).click();
  await page.getByRole('menuitem', { name: 'Details', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('textarea[aria-label="章节内容"]')?.value.includes('Keep this section'));
  await page.getByRole('button', { name: '选择章节：Details', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Feature 0', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('textarea[aria-label="章节内容"]')?.value.includes('Feature'));
  await page.getByLabel('章节内容').fill('# Feature 0\nUpdated only this section.\n');
  await page.getByRole('button', { name: '保存章节', exact: true }).click();
  await page.waitForFunction(() => !document.querySelector('.draft-list'));
  assert.equal(await readFile(editPath, 'utf8'), '# Feature 0\nUpdated only this section.\n## Details\nKeep this section.\n');
  checks.push('跨路由草稿保留、文件冲突处理与章节字节保护');

  await open('/memories/scope');
  for (const [label, option, parameter, expected] of [
    ['平台', 'codex', 'platform', 'codex'], ['配置档案', 'default', 'profile', 'default'],
    ['作用层级', '用户', 'level', 'user'], ['处理阶段', '后台整合', 'stage', 'background'],
  ]) {
    await page.getByRole('button', { name: `${label}：全部`, exact: true }).click();
    await page.getByRole('menuitem', { name: option, exact: true }).click();
    await settled();
    assert.equal(new URL(page.url()).searchParams.get(parameter), expected);
    await page.getByRole('button', { name: '清除筛选', exact: true }).click(); await settled();
  }
  await page.getByRole('button', { name: '适用项目：全部', exact: true }).click();
  await page.getByLabel('搜索适用项目', { exact: true }).fill(project);
  const projectOption = page.getByRole('menuitem', { name: `project ${project}`, exact: true });
  assert.equal(await projectOption.locator('.filter-option-name').textContent(), 'project');
  assert.equal(await projectOption.locator('.filter-option-path').textContent(), project);
  await projectOption.click();
  await settled();
  assert.equal(new URL(page.url()).searchParams.get('project'), project);
  await page.getByRole('button', { name: '清除筛选', exact: true }).click(); await settled();
  await page.getByRole('button', { name: /项目记忆/ }).click();
  await page.getByRole('link', { name: '所在目录', exact: true }).click();
  await page.waitForURL('**/memories/directory?**');
  await settled();
  assert.equal(new URL(page.url()).searchParams.get('path'), memory);
  assert.match(await page.locator('.stats').innerText(), /本层记忆 1/);
  assert.equal(await page.locator('.memory-list article').count(), 1);
  await page.getByLabel('包含子目录文件').click();
  await page.waitForFunction(() => new URL(location.href).searchParams.get('recursive') === 'true');
  await settled();
  await page.waitForFunction(() => document.querySelectorAll('.memory-list article').length === 2);
  assert.equal(await page.getByLabel('包含子目录文件').isChecked(), true);
  await page.getByRole('button', { name: '关闭文件查看器' }).click();
  await page.locator('.folder-list button').click(); await settled();
  await page.waitForFunction(expected => new URL(location.href).searchParams.get('path') === expected, path.join(memory, 'extensions'));
  assert.equal(new URL(page.url()).searchParams.get('path'), path.join(memory, 'extensions'));
  checks.push('记忆真实目录、直接子目录与子目录文件统计');

  await open('/reviews');
  await page.getByRole('button', { name: '使用记录窗口：30 天', exact: true }).click();
  const reviewMenu = page.getByRole('menu', { name: '使用记录窗口选项', exact: true });
  const resizable = page.locator('.filter-popup').filter({ has: reviewMenu });
  await reviewMenu.waitFor();
  await page.locator('.filter-dropdown').filter({ has: reviewMenu }).evaluate(async element => {
    await Promise.all(element.getAnimations({ subtree: true }).map(animation => animation.finished));
  });
  await resizable.hover({ position: { x: 6, y: 6 } });
  const beforeResize = await resizable.boundingBox();
  assert(beforeResize);
  assert(await reviewMenu.locator('.filter-option-text > span').evaluateAll(elements => elements.every(element => element.scrollWidth <= element.clientWidth + 1)));
  const menuHeight = await reviewMenu.evaluate(element => element.clientHeight);
  const lastItem = await reviewMenu.getByRole('menuitem').last().boundingBox();
  const menuBox = await reviewMenu.boundingBox();
  assert(lastItem && menuBox && menuBox.y + menuHeight - lastItem.y - lastItem.height <= 8);
  assert(Math.abs(beforeResize.height - menuHeight - 8) < 1);
  assert.equal(await reviewMenu.getByRole('menuitem', { name: '30 天', exact: true }).evaluate(element => getComputedStyle(element).backgroundColor), 'rgb(238, 244, 236)');
  await page.mouse.move(beforeResize.x + beforeResize.width - 6, beforeResize.y + beforeResize.height - 6);
  await page.mouse.down();
  await page.mouse.move(beforeResize.x + beforeResize.width + 94, beforeResize.y + beforeResize.height - 86, { steps: 12 });
  await page.mouse.up();
  const afterResize = await resizable.boundingBox();
  assert(afterResize && afterResize.width > beforeResize.width + 80 && afterResize.height < beforeResize.height - 60, JSON.stringify({ beforeResize, afterResize }));
  assert(await reviewMenu.evaluate(element => element.scrollHeight > element.clientHeight));
  await reviewMenu.hover(); await page.mouse.wheel(0, 400);
  await page.waitForFunction(() => document.querySelector('[aria-label="使用记录窗口选项"]')?.scrollTop > 0);
  await page.getByRole('menuitem', { name: '365 天', exact: true }).click(); await settled();
  assert.equal(new URL(page.url()).searchParams.get('days'), '365');
  await page.getByRole('button', { name: '未修改时间：90 天', exact: true }).click();
  await page.getByRole('menu', { name: '未修改时间选项', exact: true }).getByRole('menuitem', { name: '180 天', exact: true }).click(); await settled();
  assert.equal(new URL(page.url()).searchParams.get('staleDays'), '180');
  checks.push('全部下拉框统一、分页与章节选择、记忆筛选、审阅条件、真实拖动缩放与滚动');

  for (const [route, title] of [['/instructions', '指令地图'], ['/instructions/user', '用户级指令'], ['/reviews', '淘汰审阅'], ['/duplicates', '重复文件'], ['/file-types', '文件说明']]) {
    await open(route); assert.equal(await page.locator('main h1').textContent(), title);
  }
  await open('/file-types');
  await page.locator('.guide-card').first().click(); await settled();
  await page.waitForURL('**/file-types/*');
  await page.reload(); await settled();
  assert.equal(await page.locator('main .panel').count(), 1);
  await open('/instructions/directory?' + new URLSearchParams({ path: project }));
  await page.getByRole('button', { name: '下一页', exact: true }).click(); await settled();
  assert.equal(new URL(page.url()).searchParams.get('page'), '2');
  checks.push('全部功能路由、说明详情和指令目录分页');
  await indexControlChecks({ page, url, work, checks, service });
  const beforeCloudCancel = requests.length;
  await open('/assets?q=Searchtoken10');
  await page.getByRole('button', { name: '检索模式：本地关键词', exact: true }).click();
  await page.getByRole('menuitem', { name: '语义检索', exact: true }).click();
  await page.getByRole('button', { name: '搜索', exact: true }).click();
  await page.getByRole('dialog').waitFor();
  await page.getByRole('button', { name: '取消', exact: true }).click();
  assert(!requests.slice(beforeCloudCancel).some(request => request.method === 'POST' && /search\/execute|translate|search\/index/.test(request.url)));
  assert(requests.every(request => request.url.startsWith(url)));
  checks.push('云端操作确认前没有发送请求');
  assert.deepEqual(errors, []);
  await writeFile(path.join(work, 'result.json'), JSON.stringify({ passed: checks, browserErrors: errors, url }, null, 2));
  console.log(JSON.stringify({ passed: checks, browserErrors: errors, report: path.join(work, 'result.json') }, null, 2));
} finally {
  try { if (browser) await browser.close(); }
  finally {
    try { await lifecycle.stop(); }
    finally { await new Promise(resolve => log.end(resolve)); }
  }
}
