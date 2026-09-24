const test = require('node:test');
const assert = require('node:assert/strict');
const ui = require('../web/lifecycle.js');

function row(changes = {}) {
  return {path: '/fixture/AGENTS.md', displayPath: 'repo/AGENTS.md', version: 'v1',
    bucket: 'review', reasonText: '仅供人工审阅', canDecide: true,
    sourceStatus: 'available', estimatedSessions: 0, confirmedReads: null, mentions: 0,
    lastModified: 1, decision: null, ...changes};
}
function data(rows) {
  const counts = Object.fromEntries(Object.keys(ui.labels).map(k => [k, 0]));
  rows.forEach(r => counts[r.bucket]++);
  return {rows, counts, total: rows.length, days: 30, staleDays: 90,
    tool: 'Codex', observationScope: 'retained_logs_only', sourceStatus: 'available',
    warnings: [], generatedAt: 2000000000, sessionsScanned: 2};
}

test('queue renders every row and escapes untrusted text', () => {
  const rows = Array.from({length: 75}, (_, i) => row({
    path: '/fixture/' + i, displayPath: '<img src=x onerror=bad>', reasonText: '<script>bad</script>'
  }));
  const html = ui.queueHtml(data(rows), 'review');
  assert.equal((html.match(/class="file-row"/g) || []).length, 75);
  assert.ok(!html.includes('<img'));
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('&lt;script&gt;'));
  assert.ok(html.includes('近 30 天'));
  assert.ok(html.includes('90 天'));
  assert.ok(html.includes('已保留'));
  assert.ok(html.includes('Codex'));
});

test('unknown is not zero, and unavailable files offer no decision', () => {
  const html = ui.queueHtml(data([row({bucket: 'unknown', canDecide: false, version: null,
    estimatedSessions: null, mentions: null, sourceStatus: 'missing'})]), 'unknown');
  assert.ok(html.includes('证据不足'));
  assert.ok(html.includes('未知'));
  assert.ok(!html.includes('data-retire-action='));
  assert.ok(!html.includes('确认未用'));
});

test('keep and snooze can be reset even when their evidence bucket changes', () => {
  for (const action of ['keep', 'snooze']) {
    const html = ui.rowHtml(row({bucket: 'unknown', decision: {version: 'v1', action, until: 2100000000}}), 2000000000);
    assert.ok(html.includes('data-retire-action="reset"'));
    assert.ok(!html.includes('data-retire-action="keep"'));
  }
});

test('expired and old-version decisions do not mask current actions', () => {
  for (const decision of [{version: 'v0', action: 'keep'}, {version: 'v1', action: 'snooze', until: 1}]) {
    const html = ui.rowHtml(row({decision}), 2000000000);
    assert.ok(html.includes('data-retire-action="keep"'));
    assert.ok(!html.includes('data-retire-action="reset"'));
  }
});

test('missing and inconsistent totals are rejected', () => {
  const value = data([row()]);
  value.counts.review = 2;
  assert.throws(() => ui.queueHtml(value, 'review'), /count/);
  assert.throws(() => ui.queueHtml(data([row()]), 'other'), /bucket/);
  const bad = data([row()]); bad.total = 2;
  assert.throws(() => ui.queueHtml(bad, 'review'), /count/);
});

test('all groups are accessible; pending data has no fabricated stats', () => {
  const value = data([row()]);
  for (const key of Object.keys(ui.labels)) {
    const html = ui.queueHtml(value, key);
    assert.ok(html.includes('data-retire-bucket="' + key + '"'));
  }
  const html = ui.loadingHtml();
  assert.ok(html.includes('正在'));
  assert.ok(!html.includes('data-retire-action='));
});

test('warnings are visible without leaking HTML', () => {
  const value = data([row()]); value.sourceStatus = 'partial';
  value.warnings = ['unknown_event_time', '<img onerror=bad>'];
  const html = ui.queueHtml(value);
  assert.ok(html.includes('部分'));
  assert.ok(!html.includes('<img'));
  assert.ok(html.includes('unknown_event_time'));
});

test('evidence is escaped metadata and describes partial detail counts', () => {
  const html = ui.evidenceHtml({path:'<img>',days:30,mentions:1,estimatedSessions:0,
    confirmedReads:null,sourceStatus:'partial',detailsReturned:1,detailsTruncated:true,
    evidence:[{kind:'mention',sessionId:'<script>',log:'fixture.jsonl',line:2,timestamp:1}],warnings:[]});
  assert.ok(html.includes('路径提及'));
  assert.ok(html.includes('不完整'));
  assert.ok(html.includes('fixture.jsonl'));
  assert.ok(!html.includes('<img>'));
  assert.ok(!html.includes('<script>'));
  assert.ok(!html.includes('真实的读取'));
  assert.ok(ui.rowHtml(row(), 2000000000).includes('data-usage-evidence'));
});

test('effective paths respect index authorization and display assumptions', () => {
  const html = ui.effectiveHtml({files:[{path:'/repo/AGENTS.md',indexed:true,bytes:4,scope:'project'},
    {path:'<unindexed>',indexed:false,bytes:3,scope:'global'}],warnings:[],sourceStatus:'available'});
  assert.ok(html.includes('data-effective-open="/repo/AGENTS.md"'));
  assert.ok(html.includes('未入库'));
  assert.ok(!html.includes('<unindexed>'));
  assert.ok(html.includes('默认配置'));
});
