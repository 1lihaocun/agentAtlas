/* Pure rendering only: the host page owns requests, navigation and decisions. */
(function (root) {
  "use strict";
  const labels = Object.freeze({
    review: "待审阅", unknown: "证据不足", kept: "已保留", snoozed: "稍后处理",
    active: "有使用线索", recent: "近期修改", excluded: "保护范围"
  });
  const sourceLabels = {
    available: "可读取的已保留日志", missing: "未找到日志来源", unreadable: "日志无法读取",
    unsupported: "尚未支持或口径不匹配", partial: "日志部分缺失、损坏或采集不完整"
  };
  const escapeHtml = value => String(value == null ? "" : value).replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[ch]));
  const countText = value => Number.isInteger(value) && value >= 0 ? String(value) : "未知";

  function activeDecision(row, now) {
    const d = row.decision;
    if (!d || d.version !== row.version) return null;
    if (d.action === "keep" || (d.action === "snooze" && d.until > now)) return d;
    return null;
  }

  function rowHtml(row, now) {
    const decision = activeDecision(row, now);
    let actions = "";
    if (row.canDecide && row.version) {
      actions = decision
        ? '<button class="btn" data-retire-action="reset">恢复待审</button>'
        : '<button class="btn" data-retire-action="keep">保留此版本</button> ' +
          '<button class="btn" data-retire-action="snooze">30 天后再看</button>';
    }
    const age = Number.isFinite(row.lastModified) && now >= row.lastModified
      ? Math.floor((now - row.lastModified) / 86400) + " 天前修改" : "修改时间未知";
    const retainedDecision = decision && row.bucket !== "kept" && row.bucket !== "snoozed"
      ? ' · ' + (decision.action === "keep" ? "已保留此版本" : "已暂缓此版本") : "";
    const evidence = '<span class="sec-chars">推算曝光 ' + countText(row.estimatedSessions) +
      ' · 已确认读取 ' + countText(row.confirmedReads) + ' · 路径提及 ' + countText(row.mentions) + '</span>';
    return '<tr class="file-row" data-path="' + escapeHtml(row.path) + '" data-version="' +
      escapeHtml(row.version) + '"><td class="path">' + escapeHtml(row.displayPath || row.path) +
      '<br>' + evidence + '</td><td><span>' + escapeHtml(labels[row.bucket] || "证据不足") +
      escapeHtml(retainedDecision) + '</span><br><span class="sec-chars">' + escapeHtml(age) +
      '</span></td><td>' + escapeHtml(row.reasonText || row.reason || "证据不足") +
      '<br><span class="sec-chars">' + escapeHtml(sourceLabels[row.sourceStatus] || row.sourceStatus) +
      '</span></td><td><button class="btn" data-usage-evidence="' + escapeHtml(row.path) +
      '">查看证据</button> ' + actions + '</td></tr>';
  }

  function validate(data) {
    if (!data || !Array.isArray(data.rows) || !data.counts || data.total !== data.rows.length) {
      throw new Error("queue count mismatch");
    }
    const actual = Object.fromEntries(Object.keys(labels).map(k => [k, 0]));
    const paths = new Set();
    for (const row of data.rows) {
      if (!Object.prototype.hasOwnProperty.call(actual, row.bucket)) throw new Error("unknown bucket");
      if (paths.has(row.path)) throw new Error("duplicate queue path");
      paths.add(row.path);
      actual[row.bucket]++;
    }
    for (const key of Object.keys(labels)) {
      if (actual[key] !== data.counts[key]) throw new Error("queue count mismatch");
    }
  }

  function queueHtml(data, selected = "review") {
    validate(data);
    if (!Object.prototype.hasOwnProperty.call(labels, selected)) throw new Error("unknown bucket");
    const tabs = Object.keys(labels).map(key => '<button class="btn' + (key === selected ? ' primary' : '') +
      '" data-retire-bucket="' + key + '" aria-pressed="' + (key === selected ? 'true' : 'false') + '">' +
      labels[key] + ' ' + data.counts[key] + '</button>').join(' ');
    const rows = data.rows.filter(row => row.bucket === selected);
    const warnings = data.warnings || [];
    const warningHtml = warnings.length
      ? '<details><summary>观测限制（' + warnings.length + '）</summary><ul>' +
        warnings.map(w => '<li>' + escapeHtml(typeof w === 'string' ? w : (w.message || w.code || JSON.stringify(w))) + '</li>').join('') +
        '</ul></details>' : '';
    const body = rows.length
      ? '<div style="overflow-x:auto"><table class="ft"><thead><tr><th>文件 / 信号</th><th>状态</th>' +
        '<th>依据</th><th>仅记录审阅决定</th></tr></thead><tbody>' +
        rows.map(r => rowHtml(r, data.generatedAt)).join('') + '</tbody></table></div>'
      : '<p class="sec-chars">此分组没有文件。证据不足的文件不会被当作零使用。</p>';
    return '<div class="g" id="retirement-review"><b>文件淘汰审阅 · 不自动删除</b>' +
      '<p class="sec-chars">近 ' + escapeHtml(data.days) + ' 天 · 至少 ' + escapeHtml(data.staleDays) +
      ' 天未修改 · Codex 已保留日志范围。推算不是确认注入，路径提及不是读取，未知不等于零。</p>' +
      '<p class="sec-chars">来源：' + escapeHtml(sourceLabels[data.sourceStatus] || data.sourceStatus) +
      ' · 共 ' + data.total + ' 个文件；全部分组计数闭合。不代表其他工具或完整历史。</p>' +
      warningHtml + '<div style="display:flex;flex-wrap:wrap;gap:6px;margin:10px 0">' + tabs +
      '</div>' + body + '</div>';
  }

  function loadingHtml() {
    return '<div class="g"><b>文件淘汰审阅</b><p class="sec-chars">正在读取观测证据，不自动修改原文件…</p></div>';
  }

  function evidenceHtml(data) {
    const rows = data.evidence || [];
    const stamp = ts => Number.isFinite(ts) ? new Date(ts * 1000).toISOString() : "时间未知";
    return '<div class="dp-head">观测证据 · ' + escapeHtml(data.path) +
      ' <button class="btn" data-evidence-close>关闭</button></div>' +
      '<p>推算曝光 ' + countText(data.estimatedSessions) + ' · 路径提及 ' + countText(data.mentions) +
      ' · 已确认读取 ' + countText(data.confirmedReads) + '</p>' +
      '<p class="sec-chars">Codex 已保留日志 · ' + escapeHtml(sourceLabels[data.sourceStatus] || data.sourceStatus) +
      '。路径提及不等于读取，推算不等于确认注入。仅显示元数据，不显示会话正文。</p>' +
      '<p class="sec-chars">窗口：' + escapeHtml(stamp(data.windowStart)) + ' — ' + escapeHtml(stamp(data.windowEnd)) +
      '；显示 ' + rows.length + ' 条去重明细' + (data.detailsTruncated ? '（全局明细达到上限，列表可能不完整）' : '') + '。</p>' +
      (rows.length ? rows.map(row => '<div class="rs-session"><b>' +
        escapeHtml(row.kind === 'estimated' ? '推算曝光' : row.kind === 'mention' ? '路径提及' : '未知证据') +
        '</b> · ' + escapeHtml(stamp(row.timestamp)) + '<div class="sec-chars">会话 ' + escapeHtml(row.sessionId) +
        '</div><div>' + escapeHtml(row.log) + ' · L' + escapeHtml(row.line) + '</div></div>').join('') :
        '<p>此范围内没有可展示的明细；不能据此断言未使用。</p>') +
      '<p class="sec-chars">按默认配置与当前文件布局推算，未核验历史注入和本机配置。</p>';
  }

  function effectiveHtml(data) {
    const rows = data.files || [];
    return '<p class="sec-chars">仅 Codex · 默认配置假设 · 当前布局，不是历史加载证明。' +
      '已入库文件可打开查看，未入库项只显示路径。</p>' +
      (rows.length ? rows.map(row => '<div class="eff-lvl"><span class="sec-chars">' +
        (row.scope === 'global' ? '用户级' : '项目级') + ' · ' + escapeHtml(row.bytes) + ' B' +
        (row.truncated ? ' · 部分预算' : '') + '</span><div>' +
        (row.indexed ? '<button class="btn" data-effective-open="' + escapeHtml(row.path) + '">' +
          escapeHtml(row.path) + '</button>' : escapeHtml(row.path) + '（未入库）') + '</div></div>').join('') :
        '<p>没有可确认列出的推算路径；请结合下方限制，勿视为没有规则。</p>') +
      ((data.warnings || []).length ? '<p class="sec-chars">观测限制：' + data.warnings.map(escapeHtml).join('；') + '</p>' : '');
  }

  const exported = {labels, escapeHtml, rowHtml, queueHtml, loadingHtml, evidenceHtml, effectiveHtml};
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  else root.AtlasLifecycle = exported;
})(typeof window !== "undefined" ? window : globalThis);
