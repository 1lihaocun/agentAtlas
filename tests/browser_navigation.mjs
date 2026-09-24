// Read-only browser acceptance of navigation/count/snapshot fixes.
// CDP_PORT=<isolated Chrome port> ATLAS_URL=http://127.0.0.1:7788 node tests/browser_navigation.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
const port=process.env.CDP_PORT;
if(!port) throw new Error('CDP_PORT is required');
const base=process.env.ATLAS_URL || 'http://127.0.0.1:7788';
const tab=await(await fetch(`http://127.0.0.1:${port}/json/new?about:blank`,{method:'PUT'})).json();
const ws=new WebSocket(tab.webSocketDebuggerUrl);
await new Promise(resolve=>ws.addEventListener('open',resolve,{once:true}));
let serial=0;const pending=new Map(), errors=[], requests=[], external=[], writes=[];
ws.addEventListener('message',event=>{
 const msg=JSON.parse(event.data);
 if(msg.id && pending.has(msg.id)){const p=pending.get(msg.id);pending.delete(msg.id);msg.error?p.reject(new Error(JSON.stringify(msg.error))):p.resolve(msg.result);}
 if(msg.method==='Runtime.exceptionThrown')errors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
 if(msg.method==='Network.requestWillBeSent'){
  const r=msg.params.request;
  if(/^https?:/.test(r.url)){const u=new URL(r.url);requests.push({path:u.pathname,method:r.method});if(u.origin!==new URL(base).origin)external.push(u.origin);if(r.method!=='GET')writes.push(r.url);}
 }
});
const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++serial;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});
async function evaluate(expression){const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);return r.result.value;}
async function wait(expression,timeout=30000){const end=Date.now()+timeout;while(Date.now()<end){if(await evaluate(expression))return;await new Promise(r=>setTimeout(r,80));}throw new Error('Timeout: '+expression);}
async function click(selector){assert(await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)return false;e.click();return true;})()`),'Missing '+selector);}
async function input(selector,value,event='input'){await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.value=${JSON.stringify(value)};e.dispatchEvent(new Event(${JSON.stringify(event)},{bubbles:true}));})()`);}
async function screenshot(name){if(!process.env.ARTIFACT_DIR)return;const r=await send('Page.captureScreenshot',{format:'png'});fs.mkdirSync(process.env.ARTIFACT_DIR,{recursive:true});fs.writeFileSync(path.join(process.env.ARTIFACT_DIR,name+'.png'),Buffer.from(r.data,'base64'));}
try{
 await send('Page.enable');await send('Runtime.enable');await send('Network.enable');
 await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await send('Page.navigate',{url:base+'/?v=logic-navigation'});
 await wait('typeof ASSETS!=="undefined" && ASSETS?.files.length && DATA?.files.length && assetStatus?.job?.running===false');
 const snapshots=await evaluate('document.querySelector("#snapshot-status").textContent');
 assert(snapshots.includes('指令地图快照') && snapshots.includes('文件库快照'));
 // Real local keyword search; navigation must restore its cached results.
 await input('#search-mode','keyword','change');await input('#search','记忆');
 await wait('searchState.q==="记忆" && searchState.results?.length && !searchState.loading',60000);
 const search=await evaluate('({q:searchState.q,mode:searchState.mode,total:searchState.total,paths:searchState.results.map(r=>r.path)})');
 await click('[data-nav=all]');const beforeBack=requests.length;await click('#btn-back');
 assert.deepEqual(await evaluate('({q:searchState.q,mode:searchState.mode,total:searchState.total,paths:searchState.results.map(r=>r.path)})'),search);
 assert(!requests.slice(beforeBack).some(r=>r.path==='/api/search'),'back must use the cache');
 // Real memory filtering/page -> locate directory -> back. No source save.
 await click('[data-nav=memories]');await wait('MEMORIES && !memoryLoading');
 await click('#memory-by-scope');await input('#memory-platform','codex','change');await wait('MEMORIES && !memoryLoading && memoryFilters.platform==="codex"');
 await input('#memory-search','memory');
 await click('#memory-next');
 const memory=await evaluate('({view:{...view},filters:{...memoryFilters},local:{...memoryLocal},page:memoryPage,recursive:memoryRecursive})');
 assert(memory.page>0,'real inventory must have multiple matching pages');
 await click('#content [data-asset-open]');await wait('current && !viewerLoading');
 assert(await evaluate('current.category==="memory" && current.memoryStorage?.directory'));
 const source=await evaluate('({path:current.path,hash:current.sha256})');
 await evaluate('document.querySelector("#v-text").value+="\\nUNSAVED NAVIGATION TEST";document.querySelector("#v-text").setSelectionRange(2,6);document.querySelector("#v-text").scrollTop=20');
 const draft=await evaluate('({value:document.querySelector("#v-text").value,start:document.querySelector("#v-text").selectionStart,end:document.querySelector("#v-text").selectionEnd,scroll:document.querySelector("#v-text").scrollTop})');
 await click('#v-directory');await wait('MEMORIES && !memoryLoading && view.mode==="directory"');
 assert.equal(await evaluate('memoryFilters.platform'), '');
 const beforeMemoryBack=requests.length;await click('#btn-back');
 assert.deepEqual(await evaluate('({view:{...view},filters:{...memoryFilters},local:{...memoryLocal},page:memoryPage,recursive:memoryRecursive})'),memory);
 assert.deepEqual(await evaluate('({value:document.querySelector("#v-text").value,start:document.querySelector("#v-text").selectionStart,end:document.querySelector("#v-text").selectionEnd,scroll:document.querySelector("#v-text").scrollTop})'),draft);
 assert(!requests.slice(beforeMemoryBack).some(r=>r.path==='/api/memories'),'same-catalog memory history uses its cache');
 const readback=await(await fetch(base+'/api/file?path='+encodeURIComponent(source.path))).json();assert.equal(readback.data.sha256,source.hash);
 await evaluate('document.querySelector("#v-text").value=editorText(current.content)');await click('#v-close');
 await screenshot('logic-memory-back');
 // Real worktree project: count, bytes and unhide agree with visible rows.
 await click('[data-nav=all]');
 const target=await evaluate('(()=>{const f=DATA.files.find(f=>f.worktree);return f?{root:f.root,proj:fileProjectKey(f)}:null;})()');
 assert(target,'worktree acceptance needs at least one real worktree entry');
 if(!await evaluate('hideWt'))await click('#btn-wt');
 await evaluate(`setView({type:'proj',root:${JSON.stringify(target.root)},proj:${JSON.stringify(target.proj)}})`);
 const counts=await evaluate('(()=>{const all=mapFiles(view.root,view.proj),shown=filesOf(view.root,view.proj);return {total:all.length,shown:shown.length,bytes:shown.reduce((s,f)=>s+f.bytes,0),rows:document.querySelectorAll("#content tr.file-row").length,stats:document.querySelector("#stats").textContent,unhide:!!document.querySelector("#btn-wt")};})()');
 assert(counts.shown<counts.total);assert.equal(counts.rows,counts.shown);assert(counts.unhide);
 assert(counts.stats.includes('当前显示文件 / 总计 '+counts.total));
 await screenshot('logic-worktree-counts');
 await send('Emulation.setDeviceMetricsOverride',{width:1024,height:800,deviceScaleFactor:1,mobile:false});
 const layout=await evaluate('({viewport:innerWidth,doc:document.documentElement.scrollWidth,content:document.querySelector("#content").scrollWidth,width:document.querySelector("#content").clientWidth,buttonRight:document.querySelector("#btn-rescan").getBoundingClientRect().right})');
 assert(layout.doc<=layout.viewport+1 && layout.content<=layout.width+1 && layout.buttonRight<=layout.viewport+1,JSON.stringify(layout));
 await screenshot('logic-worktree-compact');
 await click('#btn-wt');assert.equal(await evaluate('document.querySelectorAll("#content tr.file-row").length'),counts.total);
 await click('[data-nav=user]');
 const userText=await evaluate('document.querySelector("#content").textContent');assert(userText.includes('位置候选'));assert(!userText.includes('项目级文件会覆盖它们'));
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);assert.deepEqual(writes,[]);
 console.log(JSON.stringify({searchBackRestored:true,searchResultCount:search.paths.length,memoryPageRestored:memory.page,draftPreserved:true,sourceUnchanged:true,worktree:{shown:counts.shown,total:counts.total,bytes:counts.bytes},snapshotStatusVisible:true,userScopeConservative:true,layout,errors,external,writes},null,2));
}finally{ws.close();await fetch(`http://127.0.0.1:${port}/json/close/${tab.id}`);}
