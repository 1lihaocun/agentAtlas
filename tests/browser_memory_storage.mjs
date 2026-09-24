// Read-only real-browser acceptance. Start an isolated headless Chrome first.
// CDP_PORT=<port> ATLAS_URL=http://127.0.0.1:7788 node tests/browser_memory_storage.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const port = process.env.CDP_PORT;
if (!port) throw new Error('Set CDP_PORT to an isolated Chrome debugging port.');
const base = process.env.ATLAS_URL || 'http://127.0.0.1:7788';
const tab = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, {method:'PUT'})).json();
const ws = new WebSocket(tab.webSocketDebuggerUrl);
await new Promise(resolve => ws.addEventListener('open',resolve,{once:true}));
let serial = 0;
const pending = new Map(), errors = [], external = [], writes = [], memoryRequests = [];
ws.addEventListener('message', event => {
  const msg = JSON.parse(event.data);
  if (msg.id && pending.has(msg.id)) {
    const p = pending.get(msg.id); pending.delete(msg.id);
    msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result);
  }
  if (msg.method === 'Runtime.exceptionThrown') errors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
  if (msg.method === 'Network.requestWillBeSent') {
    const request = msg.params.request;
    if (/^https?:/.test(request.url)) {
      const url = new URL(request.url);
      if (url.origin !== new URL(base).origin) external.push(url.origin);
      if (request.method !== 'GET') writes.push({method:request.method,path:url.pathname});
      if (url.pathname === '/api/memories') memoryRequests.push(request.url);
    }
  }
});
const send = (method, params={}) => new Promise((resolve,reject) => {
  const id = ++serial; pending.set(id,{resolve,reject}); ws.send(JSON.stringify({id,method,params}));
});
async function evaluate(expression) {
  const result = await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
  return result.result.value;
}
async function wait(expression, timeout=30000) {
  const deadline = Date.now()+timeout;
  while (Date.now()<deadline) {
    if (await evaluate(expression)) return;
    await new Promise(resolve=>setTimeout(resolve,80));
  }
  throw new Error('Timeout: '+expression);
}
async function clickDirectory(directory) {
  assert(await evaluate(`(() => {const node=[...document.querySelectorAll('[data-memory-dir]')].find(e=>e.dataset.memoryDir===${JSON.stringify(directory)});if(!node)return false;node.click();return true;})()`),'directory must be reachable through actual UI: '+directory);
  await wait(`view.directory===${JSON.stringify(directory)} && view.mode==='directory'`);
}
async function screenshot(name) {
  if (!process.env.ARTIFACT_DIR) return;
  const image = await send('Page.captureScreenshot',{format:'png'});
  fs.mkdirSync(process.env.ARTIFACT_DIR,{recursive:true});
  fs.writeFileSync(path.join(process.env.ARTIFACT_DIR,name+'.png'),Buffer.from(image.data,'base64'));
}
const displayedPaths = () => evaluate("[...document.querySelectorAll('#content .memory-path')].map(e=>e.title)");
async function collectPages() {
  const found = [];
  for (let page=0;page<200;page++) {
    const rows = await displayedPaths(); assert(rows.length<=50); found.push(...rows);
    if (await evaluate('document.querySelector("#memory-next").disabled')) return found;
    await evaluate('document.querySelector("#memory-next").click()');
  }
  throw new Error('pagination never terminated');
}
try {
  await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
  await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await send('Page.navigate',{url:base});
  await wait('typeof ASSETS!=="undefined" && ASSETS?.files.length>0 && DATA?.files.length>0');
  await evaluate('document.querySelector("[data-nav=memories]").click()');
  await wait('MEMORIES && !memoryLoading && !memoryError');
  const tree = await evaluate('MEMORIES.storageTree'); assert(tree?.roots.length);
  const expected = await evaluate('MEMORIES.files.map(f=>f.path)');
  assert.equal(tree.totalFiles,expected.length);
  assert.equal(tree.unplacedFiles.length,0);
  await evaluate('document.querySelector("#memory-by-directory").click()');
  assert.equal(await evaluate('view.mode'),'directory');
  assert.deepEqual(await displayedPaths(),[]);
  const before = memoryRequests.length, found = [], visited = [];
  async function visit(directory) {
    await clickDirectory(directory);
    visited.push(directory);
    const own = await collectPages();
    assert.deepEqual(own.slice().sort(),tree.nodes[directory].directFiles.slice().sort());
    found.push(...own);
    for (const child of tree.nodes[directory].children) { await visit(child); await clickDirectory(directory); }
  }
  for (const root of tree.roots) { await visit(root); await clickDirectory(''); }
  assert.equal(new Set(found).size,found.length);
  assert.deepEqual(found.slice().sort(),expected.slice().sort());
  assert.equal(visited.length,Object.keys(tree.nodes).length);
  assert.equal(memoryRequests.length,before,'tree traversal must not request source bodies or reload metadata');
  const codex = tree.roots.find(r=>r.includes('/.codex/memories')) || tree.roots[0];
  await clickDirectory(codex);
  await screenshot('memory-directories');
  await evaluate('document.querySelector("#memory-recursive").click()');
  assert.deepEqual((await collectPages()).sort(),tree.nodes[codex].filePaths.slice().sort());
  await evaluate('document.querySelector("#memory-recursive").click()');
  const child = tree.nodes[codex].children[0];
  if (child) {
    await clickDirectory(child);
    await evaluate('document.querySelector("#btn-back").click()');
    assert.equal(await evaluate('view.directory'),codex);
  }
  await evaluate('document.querySelector("#memory-by-scope").click()');
  const source = await evaluate("MEMORIES.files.find(f=>f.editable===true && f.platform==='hermes' && f.memoryStorage && f.searchable!==false)?.path");
  assert(source,'a safe editable memory fixture must exist in this real inventory');
  const sourceLocation = tree.locations[source];
  await evaluate('document.querySelector("#memory-by-directory").click()');
  for (const ancestor of sourceLocation.ancestors) await clickDirectory(ancestor);
  await evaluate(`([...document.querySelectorAll('[data-asset-open]')].find(e=>e.dataset.assetOpen===${JSON.stringify(source)})).click()`);
  await wait(`current?.path===${JSON.stringify(source)} && !viewerLoading`);
  const originalHash = await evaluate('current.sha256');
  assert.equal(await evaluate('current.memoryStorage.directory'),sourceLocation.directory);
  await evaluate('document.querySelector("#v-text").value+="\\nUNSAVED DIRECTORY TEST";document.querySelector("#v-text").setSelectionRange(2,5);document.querySelector("#memory-by-scope").click()');
  await evaluate('document.querySelector("#memory-platform").value="codex";document.querySelector("#memory-platform").dispatchEvent(new Event("change"))');
  await wait('MEMORIES && !memoryLoading');
  await evaluate('document.querySelector("#v-directory").click()');
  await wait('MEMORIES && !memoryLoading');
  assert.equal(await evaluate('view.directory'),sourceLocation.directory);
  assert(await evaluate('document.querySelector("#v-text").value.endsWith("UNSAVED DIRECTORY TEST") && document.querySelector("#v-text").selectionStart===2 && document.querySelector("#v-text").selectionEnd===5'));
  assert.equal(await evaluate('current.sha256'),originalHash);
  assert.deepEqual((await displayedPaths()).sort(),tree.nodes[sourceLocation.directory].directFiles.slice().sort());
  const readback = await (await fetch(base+'/api/file?path='+encodeURIComponent(source))).json();
  assert.equal(readback.data.sha256,originalHash,'real source must remain untouched');
  const overflow = await evaluate('({viewport:innerWidth,width:document.documentElement.scrollWidth,content:document.querySelector("#content").scrollWidth,available:document.querySelector("#content").clientWidth})');
  assert(overflow.width<=overflow.viewport+1,JSON.stringify(overflow));
  assert(overflow.content<=overflow.available+1,JSON.stringify(overflow));
  await evaluate('document.querySelector("#v-text").value=editorText(current.content);document.querySelector("#v-close").click()');
  await clickDirectory('');await clickDirectory(codex);
  await screenshot('memory-directories');
  await send('Emulation.setDeviceMetricsOverride',{width:1024,height:800,deviceScaleFactor:1,mobile:false});
  await screenshot('memory-directories-compact');
  assert(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'));
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);assert.deepEqual(writes,[]);
  console.log(JSON.stringify({roots:tree.roots.length,directoriesVisited:visited.length,filesVerified:found.length,
    noDuplicateOrMissingFiles:true,localNavigation:true,recursiveView:true,historyBack:true,
    viewerLocation:true,draftPreserved:true,sourceUnchanged:true,overflow,errors,external,writes},null,2));
} catch (error) {
  console.error('DIRECTORY E2E STATE',await evaluate('({view,error:memoryError,loading:memoryLoading,hasTree:!!MEMORIES?.storageTree})').catch(()=>null));
  throw error;
} finally {
  ws.close();await fetch(`http://127.0.0.1:${port}/json/close/${tab.id}`);
}
