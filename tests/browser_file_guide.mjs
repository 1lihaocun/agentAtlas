// Read-only real-browser acceptance; use an isolated Chrome profile.
// CDP_PORT=<port> ATLAS_URL=http://127.0.0.1:7788 node tests/browser_file_guide.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const port=process.env.CDP_PORT;
if(!port) throw new Error('Set CDP_PORT to an isolated Chrome debugging port.');
const base=process.env.ATLAS_URL || 'http://127.0.0.1:7788';
const tab=await(await fetch(`http://127.0.0.1:${port}/json/new?about:blank`,{method:'PUT'})).json();
const ws=new WebSocket(tab.webSocketDebuggerUrl);
await new Promise(resolve=>ws.addEventListener('open',resolve,{once:true}));
let serial=0;
const pending=new Map(), errors=[], requests=[], external=[], writes=[];
ws.addEventListener('message', event=>{
  const msg=JSON.parse(event.data);
  if(msg.id && pending.has(msg.id)) {
    const p=pending.get(msg.id);pending.delete(msg.id);
    msg.error?p.reject(new Error(JSON.stringify(msg.error))):p.resolve(msg.result);
  }
  if(msg.method==='Runtime.exceptionThrown') errors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
  if(msg.method==='Network.requestWillBeSent') {
    const r=msg.params.request;
    if(/^https?:/.test(r.url)) {
      const u=new URL(r.url);requests.push({path:u.pathname,url:r.url,method:r.method});
      if(u.origin!==new URL(base).origin) external.push(u.origin);
      if(r.method!=='GET') writes.push({path:u.pathname,method:r.method});
    }
  }
});
const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++serial;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});
async function evaluate(expression) {
  const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
  if(r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
}
async function wait(expression,timeout=30000) {
  const deadline=Date.now()+timeout;
  while(Date.now()<deadline) {
    if(await evaluate(expression)) return;
    await new Promise(resolve=>setTimeout(resolve,80));
  }
  throw new Error('Timeout: '+expression);
}
async function click(selector) {
  assert(await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)return false;e.click();return true;})()`),'missing '+selector);
}
async function search(selector,value) {
  await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.value=${JSON.stringify(value)};e.dispatchEvent(new Event('input',{bubbles:true}));})()`);
}
async function screenshot(name) {
  if(!process.env.ARTIFACT_DIR) return;
  const r=await send('Page.captureScreenshot',{format:'png'});
  fs.mkdirSync(process.env.ARTIFACT_DIR,{recursive:true});
  fs.writeFileSync(path.join(process.env.ARTIFACT_DIR,name+'.png'),Buffer.from(r.data,'base64'));
}
try {
  await send('Page.enable');await send('Runtime.enable');await send('Network.enable');
  await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await send('Page.navigate',{url:base+'/?v=file-guide-acceptance'});
  await wait('typeof ASSETS!=="undefined" && ASSETS?.files.length>0 && DATA?.files.length>0');
  assert(await evaluate('document.querySelector("#btn-file-guide").getBoundingClientRect().height<44'), 'header action labels must stay on one line');
  const book=await(await fetch(base+'/api/file-guide')).json();assert(book.ok);
  await click('#btn-file-guide');
  await wait('fileGuideState.library && fileGuideState.detail && !fileGuideController');
  const seen=[];
  for(const type of book.data.types) {
    assert(await evaluate(`(()=>{const e=[...document.querySelectorAll('[data-guide-type]')].find(e=>e.dataset.guideType===${JSON.stringify(type.id)});if(!e)return false;e.click();return true;})()`));
    assert.equal(await evaluate('fileGuideState.selectedId'),type.id);
    assert((await evaluate('document.querySelector("#file-guide-detail").textContent')).includes(type.label));
    seen.push(type.id);
  }
  assert.equal(new Set(seen).size,book.data.types.length);
  let before=requests.length;
  await search('#file-guide-search','UTC');
  const filtered=await evaluate("[...document.querySelectorAll('[data-guide-type]')].map(e=>e.dataset.guideType)");
  assert.deepEqual(filtered.sort(),book.data.types.filter(t=>JSON.stringify(t).toLowerCase().includes('utc')).map(t=>t.id).sort());
  assert.equal(requests.length,before,'guide search is local');
  await search('#file-guide-search','');await click('#file-guide-close');
  // The ordinary asset row action must not accidentally bubble into file preview.
  before=requests.length;
  await click('#content [data-guide-path]');
  await wait('fileGuideState.path && fileGuideState.detail?.path===fileGuideState.path && !fileGuideController');
  assert(requests.slice(before).every(r=>r.path==='/api/file-guide'));
  assert.equal(await evaluate('current'),null);
  await click('#file-guide-close');
  await click('[data-nav=memories]');await wait('MEMORIES && !memoryLoading && !memoryError');
  await click('#memory-by-scope');
  const source=await evaluate("MEMORIES.files.find(f=>f.path.includes('/extensions/skysight/resources/') && f.name.includes('memory-summary'))?.path");
  assert(source);
  await search('#memory-search',path.basename(source));
  await wait('document.querySelector("#content [data-guide-path]")');
  const filtersBefore=await evaluate('JSON.stringify({view,memoryFilters,memoryLocal,memoryPage,memoryRecursive})');
  before=requests.length;
  await click('#content [data-guide-path]');
  await wait('fileGuideState.detail?.filename.status==="matched" && !fileGuideController');
  assert.equal(await evaluate('fileGuideState.path'),source);
  assert.equal(await evaluate('fileGuideState.detail.filename.original'),path.basename(source));
  const parts=await evaluate('fileGuideState.detail.filename.fields');
  assert.equal(parts.length,5);assert(parts.find(f=>f.key==='timestamp').meaning.includes('UTC'));
  assert(await evaluate('document.querySelector(".guide-fields tbody tr:first-child td:first-child code").getClientRects().length===1'), 'filename field keys should not split into single trailing letters');
  assert((await evaluate('document.querySelector("#file-guide-detail").textContent')).includes('26.917.51856'));
  assert(requests.slice(before).every(r=>r.path==='/api/file-guide'));
  assert.equal(await evaluate('JSON.stringify({view,memoryFilters,memoryLocal,memoryPage,memoryRecursive})'),filtersBefore);
  await screenshot('file-guide-filename');
  await evaluate('document.querySelector("#file-guide-detail").scrollTop=180');
  const scroll=await evaluate('document.querySelector("#file-guide-detail").scrollTop');
  await click('#file-guide-refresh');await wait('!fileGuideController && fileGuideState.detail?.path===fileGuideState.path');
  assert.equal(await evaluate('document.querySelector("#file-guide-detail").scrollTop'),scroll);
  await send('Emulation.setDeviceMetricsOverride',{width:1024,height:800,deviceScaleFactor:1,mobile:false});
  await screenshot('file-guide-compact');
  const layout=await evaluate("(()=>{const d=document.querySelector('#file-guide-dialog'),p=document.querySelector('#file-guide-detail');return {viewport:innerWidth,doc:document.documentElement.scrollWidth,dialog:d.scrollWidth,width:d.clientWidth,detail:p.scrollWidth,detailWidth:p.clientWidth,headerRight:document.querySelector('#btn-rescan').getBoundingClientRect().right};})()");
  assert(layout.doc<=layout.viewport+1 && layout.dialog<=layout.width+1 && layout.detail<=layout.detailWidth+1,JSON.stringify(layout));
  assert(layout.headerRight<=layout.viewport+1,'header controls must remain reachable: '+JSON.stringify(layout));
  await click('#file-guide-close');
  const database=await evaluate("MEMORIES.files.find(f=>f.memoryInfo?.status==='metadata_only')?.path");assert(database);
  await search('#memory-search',path.basename(database));
  before=requests.length;await click('#content [data-guide-path]');
  await wait(`fileGuideState.detail?.path===${JSON.stringify(database)} && !fileGuideController`);
  assert(requests.slice(before).every(r=>r.path==='/api/file-guide'));
  assert.equal(await evaluate('current'),null,'metadata-only explanation cannot open the database');
  await click('#file-guide-close');
  const editable=await evaluate("MEMORIES.files.find(f=>f.platform==='hermes' && f.editable===true && f.path.endsWith('/USER.md') && f.searchable!==false)?.path");assert(editable);
  await search('#memory-search',editable);
  await click('#content [data-asset-open]');await wait(`current?.path===${JSON.stringify(editable)} && !viewerLoading`);
  const hash=await evaluate('current.sha256');
  assert(await evaluate('!document.querySelector("#v-file-guide").hidden && document.querySelector("#v-file-guide-summary").textContent.includes("Hermes")'));
  await evaluate('document.querySelector("#v-text").value+="\\nUNSAVED GUIDE TEST";document.querySelector("#v-text").setSelectionRange(2,5);document.querySelector("#v-text").scrollTop=20');
  const draft=await evaluate('({text:document.querySelector("#v-text").value,start:document.querySelector("#v-text").selectionStart,end:document.querySelector("#v-text").selectionEnd,scroll:document.querySelector("#v-text").scrollTop})');
  before=requests.length;await click('#v-file-guide-open');
  await wait(`fileGuideState.detail?.path===${JSON.stringify(editable)} && !fileGuideController`);
  await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
  await send('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
  await wait('!document.querySelector("#file-guide-dialog").open');
  assert.deepEqual(await evaluate('({text:document.querySelector("#v-text").value,start:document.querySelector("#v-text").selectionStart,end:document.querySelector("#v-text").selectionEnd,scroll:document.querySelector("#v-text").scrollTop})'),draft);
  assert(requests.slice(before).every(r=>r.path==='/api/file-guide'));
  const readback=await(await fetch(base+'/api/file?path='+encodeURIComponent(editable))).json();assert.equal(readback.data.sha256,hash);
  await evaluate('document.querySelector("#v-text").value=editorText(current.content)');await click('#v-close');
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);assert.deepEqual(writes,[]);
  console.log(JSON.stringify({typesVerified:seen.length,filenameParts:parts.length,utcReaderEvidence:true,metadataOnlySafe:true,draftPreserved:true,sourceUnchanged:true,localSearch:true,refreshPreserved:true,layout,errors,external,writes},null,2));
} finally {
  ws.close();await fetch(`http://127.0.0.1:${port}/json/close/${tab.id}`);
}
