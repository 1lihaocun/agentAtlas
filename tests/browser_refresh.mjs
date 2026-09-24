// Explicit local-rescan acceptance. Changes only AgentAtlas snapshots/index.
// CDP_PORT=<isolated Chrome> ATLAS_RESCAN=1 ATLAS_URL=http://127.0.0.1:7788 node tests/browser_refresh.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
if (process.env.ATLAS_RESCAN !== '1') throw new Error('Set ATLAS_RESCAN=1 to authorize one local rescan.');
const port = process.env.CDP_PORT;
if (!port) throw new Error('CDP_PORT is required');
const base = process.env.ATLAS_URL || 'http://127.0.0.1:7788';
const tab = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, {method:'PUT'})).json();
const ws = new WebSocket(tab.webSocketDebuggerUrl);
await new Promise(resolve => ws.addEventListener('open', resolve, {once:true}));
let serial = 0;
const pending = new Map(), requests = [], external = [], errors = [], blocked = [];
ws.addEventListener('message', event => {
  const message = JSON.parse(event.data);
  if (message.id && pending.has(message.id)) {
    const promise = pending.get(message.id); pending.delete(message.id);
    message.error ? promise.reject(new Error(JSON.stringify(message.error))) : promise.resolve(message.result);
  }
  if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
  if (message.method === 'Network.loadingFailed' && message.params.blockedReason) blocked.push(message.params.blockedReason);
  if (message.method === 'Network.requestWillBeSent') {
    const request = message.params.request;
    if (/^https?:/.test(request.url)) {
      const url = new URL(request.url);
      requests.push({path:url.pathname, method:request.method, body:request.postData});
      if (url.origin !== new URL(base).origin) external.push(url.origin);
    }
  }
});
const send = (method, params={}) => new Promise((resolve,reject) => {
  const id = ++serial; pending.set(id,{resolve,reject}); ws.send(JSON.stringify({id,method,params}));
});
async function evaluate(expression) {
  const result = await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
  return result.result.value;
}
async function wait(expression, timeout=150000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await evaluate(expression)) return;
    await new Promise(resolve => setTimeout(resolve,80));
  }
  throw new Error('Timeout: '+expression);
}
async function screenshot(name) {
  if (!process.env.ARTIFACT_DIR) return;
  fs.mkdirSync(process.env.ARTIFACT_DIR,{recursive:true});
  const image = await send('Page.captureScreenshot',{format:'png'});
  fs.writeFileSync(path.join(process.env.ARTIFACT_DIR,name+'.png'),Buffer.from(image.data,'base64'));
}
try {
  await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
  await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await send('Page.navigate',{url:base+'/?v=refresh-recovery'});
  await wait('typeof ASSETS!=="undefined" && ASSETS?.files.length && DATA && assetStatus?.job?.running===false');
  const source = await evaluate('ASSETS.files.find(f=>f.category==="memory" && f.platform==="hermes" && f.name==="USER.md" && f.editable)?.path');
  assert(source,'an editable Hermes USER.md is needed for a stable read-only draft check');
  await evaluate(`openFile(${JSON.stringify(source)})`);
  const hash = await evaluate('current.sha256');
  await evaluate('document.querySelector("#v-text").value+="\\nUNSAVED REFRESH ACCEPTANCE";document.querySelector("#v-text").setSelectionRange(2,6)');
  await evaluate('document.querySelector("[data-nav=memories]").click()');
  await wait('MEMORIES && !memoryLoading');
  await evaluate('document.querySelector("#memory-platform").value="hermes";document.querySelector("#memory-platform").dispatchEvent(new Event("change",{bubbles:true}))');
  await wait('MEMORIES && !memoryLoading && memoryFilters.platform==="hermes"');
  const oldVersion = await evaluate('DATA.generatedAt');
  await send('Network.setBlockedURLs',{urls:[base+'/api/assets/status*']});
  const begin = requests.length;
  const started = performance.now();
  await evaluate('document.querySelector("#btn-rescan").click()');
  await wait('!assetJobStarting && assetStatusError.includes("自动重试")');
  assert(await evaluate('document.querySelector("#btn-rescan").disabled && statusTimer!==null'));
  assert(await evaluate('document.querySelector("#snapshot-status").textContent.includes("自动重试")'),'status recovery must be visible in the memory view');
  await screenshot('refresh-status-retry');
  await send('Network.setBlockedURLs',{urls:[]});
  await wait('assetStatus?.job?.running===false && !assetStatusError && !assetJobStarting && MEMORIES && !memoryLoading && DATA.generatedAt===ASSETS.instructionGeneratedAt');
  assert(await evaluate(`DATA.generatedAt>${oldVersion}`));
  assert.equal(await evaluate('memoryFilters.platform'),'hermes');
  assert(await evaluate('document.querySelector("#v-text").value.endsWith("UNSAVED REFRESH ACCEPTANCE") && document.querySelector("#v-text").selectionStart===2 && document.querySelector("#v-text").selectionEnd===6'));
  assert.equal(await evaluate('assetStatus.job.embeddings'),false);
  const after = requests.slice(begin), writes = after.filter(r=>r.method!=='GET');
  assert.equal(writes.length,1); assert.equal(writes[0].path,'/api/rescan'); assert.deepEqual(JSON.parse(writes[0].body),{});
  assert.equal(after.filter(r=>r.path==='/api/assets').length,1,'completion fetches the full catalog only once');
  assert.equal(after.filter(r=>r.path==='/api/memories').length,1,'memory metadata reloads only once');
  const file = await (await fetch(base+'/api/file?path='+encodeURIComponent(source))).json();
  assert.equal(file.data.sha256,hash);
  const counts = await evaluate('({instructions:DATA.totalFiles,assets:ASSETS.totalFiles,memories:MEMORIES.counts.total,scanMs:DATA.durationMs})');
  await evaluate('document.querySelector("#v-text").value=editorText(current.content);document.querySelector("#v-close").click()');
  await screenshot('refresh-completed');
  await send('Emulation.setDeviceMetricsOverride',{width:1024,height:800,deviceScaleFactor:1,mobile:false});
  assert(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'));
  await screenshot('refresh-completed-compact');
  assert(blocked.length>0,'CDP must have actually blocked a status request');
  assert.deepEqual(errors,[]); assert.deepEqual(external,[]);
  const report={seconds:Math.round(performance.now()-started)/1000,counts,recoveredBlockedStatus:true,sourceHashUnchanged:true,draftPreserved:true,localScanPosts:writes.length,catalogReads:1,memoryReads:1,errors,external};
  console.log(JSON.stringify(report,null,2));
  if (process.env.ARTIFACT_DIR) fs.writeFileSync(path.join(process.env.ARTIFACT_DIR,'refresh-result.json'),JSON.stringify(report,null,2));
} finally {
  try { await send('Network.setBlockedURLs',{urls:[]}); } catch {}
  ws.close(); await fetch(`http://127.0.0.1:${port}/json/close/${tab.id}`);
}
