"""Dependency-free frontend checks; execute the actual inline JS in Node.

Run: python3 -m unittest discover -s tests -p 'test_frontend.py' -v
DOM stubs test behavior, not layout; real-browser integration is separate.
"""
import collections
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "web" / "index.html"
HTML = PAGE.read_text()
SCRIPT = "\n".join(re.findall(r"<script>([\s\S]*?)</script>", HTML))
HARNESS = r'''
const vm = require('vm'), assert = require('assert');
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const elements = new Map(), requests = [], timers = new Map();
let nextTimer = 0;
function element() {
  const classes = new Set();
  return {value:'', innerHTML:'', textContent:'', disabled:false, readOnly:false,
    checked:false, open:false, style:{}, dataset:{}, selectionStart:0, selectionEnd:0, scrollTop:0,
    classList:{add:c=>classes.add(c), remove:c=>classes.delete(c), contains:c=>classes.has(c),
      toggle(c, on){on === undefined ? (classes.has(c)?classes.delete(c):classes.add(c)) : (on?classes.add(c):classes.delete(c));}},
    listeners:{}, addEventListener(t,f){this.listeners[t]=f;}, removeEventListener(){},
    appendChild(){}, remove(){}, focus(){}, showModal(){this.open=true;}, close(){this.open=false;},
    querySelector(){return null;}, querySelectorAll(){return [];},
    setSelectionRange(a,b){this.selectionStart=a;this.selectionEnd=b;},
    getBoundingClientRect(){return {width:470,height:300,left:0,top:0,bottom:300};},
    contains(){return false;}, closest(){return null;}, setAttribute(){}, removeAttribute(){},
    getAttribute(){return null;}};
}
for (const id of input.html.matchAll(/id="([\w-]+)"/g)) elements.set('#'+id[1], element());
const document = {querySelector:s=>elements.get(s)||null, querySelectorAll:()=>[],
  addEventListener(){}, removeEventListener(){}, createElement:element, body:element(), activeElement:null};
const context = {assert, elements, requests, timers, document, console, URL, URLSearchParams, AbortController,
  window:{innerWidth:1440,innerHeight:900,addEventListener(){},removeEventListener(){}},
  localStorage:{getItem:()=>null,setItem(){}}, confirm:()=>true,
  navigator:{clipboard:{writeText:async()=>{}}},
  setTimeout(f){const id=++nextTimer;timers.set(id,f);return id;}, clearTimeout:id=>timers.delete(id),
  fetch:(url,opts)=>{requests.push({url,opts});return new Promise(()=>{});}};
vm.runInNewContext(input.script + '\n(async () => {\n' + input.test + '\n})().catch(e => {console.error(e);process.exitCode=1;});',
  {...context, process}, {timeout:5000});
'''


@unittest.skipUnless(shutil.which("node"), "Node is required for extracted JS checks")
class FrontendTests(unittest.TestCase):
    def run_js(self, test):
        result = subprocess.run(
            ["node", "-e", HARNESS],
            input=json.dumps({"html": HTML, "script": SCRIPT, "test": test}),
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_legacy_rescan_sends_json_and_settings_match_server_limits(self):
        self.run_js(r'''
const calls=[];
api=async (url,opts)=>{calls.push({url,opts});return {stdout:'done'};};
loadTree=loadAssets=refreshAssetStatus=async()=>{};
await $('#btn-rescan').listeners.click({target:$('#btn-rescan')});
const request=calls.find(c=>c.url==='/api/rescan');
assert.strictEqual(request.opts.headers['Content-Type'],'application/json');
$('#settings-base').value='https://example.test/v1/';
$('#settings-model').value='model'; $('#settings-batch').value='16';
assert.strictEqual(settingsPayload().baseUrl,'https://example.test/v1');
$('#settings-batch').value='129';
assert.throws(()=>settingsPayload());
''')

    def test_js_syntax(self):
        result = subprocess.run(["node", "--check"], input=SCRIPT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_all_selects_use_accessible_custom_dropdown(self):
        select_ids = re.findall(r'<select\b[^>]*id="([\w-]+)"', HTML)
        self.assertEqual(len(select_ids), 10)
        self.assertIn('document.querySelectorAll("select").forEach(enhanceAtlasSelect)', SCRIPT)
        self.assertIn('role", "listbox"', SCRIPT)
        self.assertIn('role", "option"', SCRIPT)
        self.assertIn('syncAtlasSelect($("#asset-" + key))', SCRIPT)
        self.assertIn('syncAtlasSelect($("#memory-level"))', SCRIPT)
        self.assertRegex(HTML, r'\.atlas-select-menu\s*\{[^}]*overflow:\s*auto')
        self.assertRegex(HTML, r'\.atlas-select-option\s*\{[^}]*width:\s*max-content')
        self.assertRegex(HTML, r'\.atlas-select-menu\.resizable\s*\{[^}]*resize:\s*both')
        self.assertIn('ui.menu.dataset.resizeReady = "true"', SCRIPT)

    def test_fixed_ids_and_unique_functions(self):
        ids = re.findall(r'id="([\w-]+)"', HTML)
        # Dynamic popovers assign .id rather than HTML attributes.
        available = set(ids) | set(re.findall(r'\.id\s*=\s*"([\w-]+)"', SCRIPT))
        used = set(re.findall(r'\$\("#([\w-]+)"\)', SCRIPT))
        self.assertFalse(used - available, used - available)
        self.assertFalse([name for name, n in collections.Counter(ids).items() if n > 1])
        functions = re.findall(r'^\s*(?:async\s+)?function\s+(\w+)\(', SCRIPT, re.M)
        self.assertFalse([name for name, n in collections.Counter(functions).items() if n > 1])

    def test_library_is_independent_paginated_and_escaped(self):
        self.run_js(r'''
assert.strictEqual(view.type, 'assets');
DATA = {files:[], roots:[], dupFiles:0};
ASSETS = {files:Array.from({length:205}, (_,i)=>({path:'/memory/'+i,name:'<img onerror=x>',
  category:'memory',platform:'Hermes',scope:'global',project:'demo',bytes:10,mtime:1})),totalFiles:205};
ASSETS.files.push({path:'/other',name:'other',category:'log',platform:'Codex',project:'else'});
assetFilters.platform='Hermes'; assetFilters.category='memory'; assetFilters.project='demo';
assetPage=0;
let page=assetPageRows();
assert.strictEqual(page.total,205); assert.strictEqual(page.rows.length,100);
assetPage=2; page=assetPageRows();
assert.strictEqual(page.rows.length,5);
assert(!assetRowHtml(page.rows[0]).includes('<img'));
assert(!assetRowHtml(page.rows[0]).includes('未用'));
assert(assetRowHtml(page.rows[0]).includes('全局位置'));
renderContent(); renderSidebar();
assert($('#content').innerHTML.includes('201–205'));
assert($('#sidebar').innerHTML.includes('指令地图'));
assert($('#sidebar').innerHTML.includes('全部文件'));
assert.strictEqual(DATA.files.length,0);
''')


    def test_search_is_explicit_for_cloud_debounced_for_keyword(self):
        self.run_js(r'''
ASSETS={files:[{path:'/a',editable:false,category:'session',bytes:42,mtime:1}],totalFiles:1};
const calls=[]; api=async (url,opts)=>{calls.push({url,opts});return {results:[{path:'/a',snippet:'<script>bad</script>',startLine:2,endLine:3}],total:91};};
$('#search').value='memo'; $('#search-mode').value='vector';
searchChanged();
assert.strictEqual(calls.length,0); assert.strictEqual(timers.size,0);
await runAssetSearch();
assert.strictEqual(calls.length,1); assert(calls[0].url.includes('mode=vector'));
assert.strictEqual(searchState.results[0].editable,false);
assert.strictEqual(searchState.results[0].bytes,42);
assert($('#content').innerHTML.includes('91')); assert($('#content').innerHTML.includes('显示 1 条'));
assert(!$('#content').innerHTML.includes('<script>bad'));
$('#search-mode').value='keyword';
assetFilters.category='memory'; assetFilters.platform='Hermes'; assetFilters.project='repo';
searchChanged(); assert.strictEqual(calls.length,1); assert.strictEqual(timers.size,1);
await [...timers.values()][0](); timers.clear();
assert.strictEqual(calls.length,2);
for (const query of ['mode=keyword','platform=Hermes','category=memory','project=repo','limit=50']) assert(calls[1].url.includes(query));
$('#search').value=''; searchChanged(); assert.strictEqual(calls.length,2);
assert.strictEqual(searchState.results,null);
''')

    def test_search_responses_cannot_override_new_filters_or_navigation(self):
        self.run_js(r'''
ASSETS={files:[],totalFiles:0}; DATA={files:[],roots:[],dupGroups:[],dupFiles:0};
$('#search').value='old'; $('#search-mode').value='vector'; searchChanged();
let resolve; api=()=>new Promise(r=>resolve=r);
const pending=runAssetSearch();
assetFilters.category='memory'; searchChanged();
resolve({results:[{path:'/stale'}],total:1}); await pending;
assert.strictEqual(searchState.results,null);
const pending2=runAssetSearch();
setView({type:'dup'}); const before=$('#content').innerHTML;
resolve({results:[{path:'/stale'}],total:1}); await pending2;
assert.strictEqual($('#content').innerHTML,before); assert.strictEqual(view.type,'dup');
assert.strictEqual($('#search').value,''); assert($('#crumb-inner'));
''')


    def test_settings_keep_blank_key_and_save_without_upload(self):
        self.run_js(r'''
const config={baseUrl:'https://example.test/v1',model:'chosen-model',dimensions:null,
  apiKeyConfigured:true,categories:['instruction','memory'],batchSize:16};
const calls=[];
api=async (url,opts)=>{calls.push({url,opts});return config;};
await openSearchSettings();
assert.strictEqual($('#settings-key').value,'');
$('#settings-categories').querySelectorAll=()=>config.categories.map(value=>({value,checked:true}));
await saveSearchSettings({preventDefault(){}});
const post=calls.find(c=>c.opts?.method==='POST');
assert.strictEqual(post.url,'/api/settings');
const body=JSON.parse(post.opts.body);
assert(!('apiKey' in body)); assert.strictEqual(body.dimensions,null);
assert(!calls.some(c=>c.url.includes('/index')));
assert.strictEqual(calls[calls.length-1].url,'/api/settings');
assert($('#settings-message').textContent.includes('未上传'));
assert.strictEqual($('#settings-key').value,'');
''')

    def test_indexing_requires_confirmation_and_status_only_polls_running(self):
        self.run_js(r'''
const config={baseUrl:'https://example.test/v1',model:'chosen-model',dimensions:null,
  apiKeyConfigured:true,categories:['memory','session'],batchSize:16};
let running=false; const calls=[];
api=async (url,opts)=>{
  calls.push({url,opts});
  if(url==='/api/settings') return config;
  if(url==='/api/assets/status') return {available:true,index:{files:2,chunks:9,vectors:0},job:{running,stage:running?'embedding':'idle'}};
  if(url==='/api/assets') return {files:[],totalFiles:0};
  running=true; return {job:{running:true,stage:'queued'}};
};
await refreshAssetStatus(); assert.strictEqual(timers.size,0);
confirm=()=>false; await startAssetJob('vector');
assert(!calls.some(c=>c.opts?.method==='POST'));
let confirmation=''; confirm=s=>{confirmation=s;return true;};
await startAssetJob('vector');
const body=JSON.parse(calls.find(c=>c.opts?.method==='POST').opts.body);
assert.strictEqual(body.embeddings,true); assert.strictEqual(body.confirmCloud,true); assert.strictEqual(body.maxChunks,256);
assert(confirmation.includes('会话')); assert(confirmation.includes('云端'));
assert.strictEqual(timers.size,1);
running=false; await refreshAssetStatus(); assert.strictEqual(timers.size,0);
calls.length=0; await startAssetJob('local');
assert.strictEqual(JSON.parse(calls.find(c=>c.opts?.method==='POST').opts.body).embeddings,false);
''')


    def test_viewer_enforces_readonly_and_selects_source_lines(self):
        self.run_js(r'''
DATA={files:[],roots:[]};
const file={path:'/x/log.jsonl',content:'alpha\nbeta\ngamma\nend',mtime:1,sha256:'original',category:'log',editable:false,truncated:true,readOnlyReason:'日志只读'};
const calls=[]; api=async (url,opts)=>{calls.push({url,opts});return file;};
await openFile(file.path,{startLine:2,endLine:3});
assert.strictEqual($('#v-text').readOnly,true); assert.strictEqual($('#v-save').disabled,true);
assert.strictEqual($('#v-open').disabled,true);
assert.strictEqual($('#v-text').value.slice($('#v-text').selectionStart,$('#v-text').selectionEnd),'beta\ngamma\n');
assert($('#v-policy').textContent.includes('只读'));
resetTr(); assert.strictEqual($('#v-save').disabled,true);
renderSections([{level:1,title:'log',chars:1,start_line:1,end_line:1,text:'<bad>'}]);
assert($('#v-sec').innerHTML.includes('readonly')); assert($('#v-sec').innerHTML.includes('disabled'));
await saveFile(); assert(!calls.some(c=>c.opts?.method==='POST'));
assert.strictEqual(isFileEditable({...file,category:'memory',editable:true,path:'/hook.sh',truncated:false}),false);
''')

    def test_save_sends_loaded_hash_and_refreshes_it_without_losing_position(self):
        self.run_js(r'''
DATA={files:[],roots:[]};
const original={path:'/memory.md',content:'old',mtime:1,sha256:'base',category:'memory',editable:true};
let file=original; const calls=[];
api=async (url,opts)=>{calls.push({url,opts});if(opts?.method==='POST'){file={...file,content:'changed',sha256:'next',mtime:2};return {changed:true,backup:'/backup/md'};}return file;};
await openFile(file.path); $('#v-text').value='changed'; $('#v-text').setSelectionRange(2,4); $('#v-text').scrollTop=50;
await saveFile();
const body=JSON.parse(calls.find(c=>c.opts?.method==='POST').opts.body);
assert.strictEqual(body.baseSha256,'base'); assert.strictEqual(current.sha256,'next');
assert.strictEqual($('#v-text').value,'changed'); assert.strictEqual($('#v-text').selectionStart,2);
assert.strictEqual($('#v-text').scrollTop,50); assert.strictEqual($('#v-save').disabled,false);
assert(calls[calls.length-1].url.startsWith('/api/file?'));
''')

    def test_viewer_races_and_asset_refresh_preserve_drafts(self):
        self.run_js(r'''
DATA={files:[],roots:[]};
const pending={}; api=url=>new Promise(r=>pending[url]=r);
const a=openFile('/a'), b=openFile('/b');
pending['/api/file?path=%2Fb']({path:'/b',content:'b',editable:true,category:'memory',sha256:'b'}); await b;
pending['/api/file?path=%2Fa']({path:'/a',content:'a',editable:true,category:'memory',sha256:'a'}); await a;
assert.strictEqual(current.path,'/b'); $('#v-text').value='unsaved';
api=async()=>({files:[],totalFiles:0}); await loadAssets();
assert.strictEqual($('#v-text').value,'unsaved');
confirm=()=>false; await openFile('/new'); assert.strictEqual(current.path,'/b');
''')


    def test_section_saves_refresh_hash_before_subsequent_saves(self):
        self.run_js(r'''
DATA={files:[],roots:[]};
let file={path:'/memory.md',content:'old',mtime:1,sha256:'h1',editable:true,category:'memory'};
const calls=[];
api=async (url,opts)=>{
  calls.push({url,opts});
  if(opts?.method==='POST'){file={...file,content:'new',sha256:file.sha256==='h1'?'h2':'h3'};return {changed:true,sha256:file.sha256};}
  if(url.startsWith('/api/sections')) return {sections:[],sha256:file.sha256,editable:true,sectionCount:0};
  return file;
};
await openFile(file.path);
const ta={value:'new',defaultValue:'old'}, btn={dataset:{secI:'0',sectionId:'versioned-section'}};
$('#v-sec').querySelector=selector=>selector.startsWith('textarea')?ta:null;
await saveSection(btn); assert.strictEqual(current.sha256,'h2');
await saveSection(btn); assert.strictEqual(current.sha256,'h3');
const bodies=calls.filter(c=>c.opts?.method==='POST').map(c=>JSON.parse(c.opts.body));
assert.strictEqual(bodies[0].baseVersion,'h1'); assert.strictEqual(bodies[1].baseVersion,'h2');
assert.strictEqual(bodies[0].sectionId,'versioned-section');
''')

    def test_translation_ignores_readonly_and_stale_completion(self):
        self.run_js(r'''
DATA={files:[],roots:[]};
api=async()=>({path:'/readonly',content:'log',editable:false,category:'log'});
await openFile('/readonly');
const calls=[]; api=async (url,opts)=>{calls.push({url,opts});return {lang:'中文',translated:'x',source:'x',errors:[]};};
await $('#v-translate').listeners.click(); assert.strictEqual(calls.length,0);
api=async()=>({path:'/memory.md',content:'old',editable:true,category:'memory',sha256:'h1'});
await openFile('/memory.md'); assert.strictEqual($('#v-translate').disabled,false);
let done; api=()=>new Promise(r=>done=r);
const pending=$('#v-translate').listeners.click();
api=async()=>({path:'/next.md',content:'next',editable:true,category:'memory',sha256:'h2'});
await openFile('/next.md');
done({lang:'中文',translated:'old translation',source:'old',errors:[],fresh:1}); await pending;
assert.strictEqual(current.path,'/next.md'); assert.strictEqual(trMode,false);
assert(!$('#v-tr').innerHTML.includes('old translation'));
''')

    def test_job_errors_remain_visible_and_unknown_counts_are_not_zero(self):
        self.run_js(r'''
api=async()=>{throw new Error('offline');};
await startAssetJob('rescan');
assert($('#index-status').textContent.includes('offline'));
api=async()=>({job:{running:false},index:{},available:false});
await refreshAssetStatus(); assert($('#index-status').textContent.includes('未知'));
assert(!$('#index-status').textContent.includes('0 文件'));
''')


    def test_instruction_tree_failure_does_not_replace_library(self):
        self.run_js(r'''
ASSETS={files:[{path:'/memory.md',name:'memory.md',category:'memory'}],totalFiles:1};
renderContent(); const before=$('#content').innerHTML;
api=async()=>{throw new Error('map unavailable');}; await loadTree();
assert.strictEqual($('#content').innerHTML,before);
''')

    def test_failed_section_reload_removes_stale_save_ranges(self):
        self.run_js(r'''
api=async()=>({path:'/memory.md',content:'old',sha256:'h1',editable:true,category:'memory'});
await openFile('/memory.md');
const ta={value:'new',defaultValue:'old'}, btn={dataset:{secI:'0',start:'1',end:'1'}};
$('#v-sec').querySelector=selector=>selector.startsWith('textarea')?ta:null;
$('#v-sec').innerHTML='<button class="sec-save" data-start="1">stale</button>';
api=async (url,opts)=>{
  if(opts?.method==='POST') return {changed:true,sha256:'h2'};
  if(url.startsWith('/api/sections')) throw new Error('sections unavailable');
  return {path:'/memory.md',content:'new',sha256:'h2',editable:true,category:'memory'};
};
await saveSection(btn);
assert.strictEqual(current.sha256,'h2');
assert(!$('#v-sec').innerHTML.includes('sec-save'));
''')

    def test_status_completion_does_not_cancel_newer_cloud_query(self):
        self.run_js(r'''
assetStatus={job:{running:true}};
let finishAssets;
api=async url=>url==='/api/assets/status'?{job:{running:false},index:{}}:new Promise(r=>finishAssets=r);
const status=refreshAssetStatus(); await Promise.resolve(); await Promise.resolve();
$('#search').value='new query'; $('#search-mode').value='vector'; searchChanged();
api=async()=>({results:[{path:'/fresh',name:'fresh'}],total:1}); await runAssetSearch();
finishAssets({files:[],totalFiles:0}); await status;
assert.strictEqual(searchState.results[0].path,'/fresh');
''')


    def test_library_keeps_translation_badges_and_old_graph_route(self):
        self.run_js(r'''
trCache['/repo/AGENTS.md']={lang:'中文'};
const f={path:'/repo/AGENTS.md',name:'AGENTS.md',root:'root',proj:'repo',sub:'',kind:'agents',bytes:10,category:'instruction'};
assert(assetRowHtml(f).includes('data-tr-path="/repo/AGENTS.md"'));
DATA={files:[f],roots:[{name:'root',files:1,bytes:10,mtime:1,projects:[{name:'repo',files:1,bytes:10,mtime:1}]}],dupFiles:0};
setView({type:'proj',root:'root',proj:'repo',mode:'graph'});
assert($('#content').innerHTML.includes('<svg')); assert($('#content').innerHTML.includes('data-path="/repo/AGENTS.md"'));
assert($('#sidebar').innerHTML.includes('data-nav="assets"'));
assert($('#sidebar').innerHTML.includes('data-nav="all"'));
''')

    def test_initial_status_does_not_claim_no_running_job(self):
        self.run_js(r'''
assetStatus=null; renderAssetStatus();
assert(!$('#index-status').textContent.includes('当前无运行任务'));
assert($('#index-status').textContent.includes('未知'));
''')


    def test_browser_newline_normalization_is_not_an_unsaved_draft(self):
        self.run_js(r'''
const file={path:'/memory.md',content:'# Notes\r\ntext\r\n',mtime:1,sha256:'crlf',editable:true,category:'memory'};
api=async()=>file; await openFile(file.path);
// Real textareas normalize CRLF to LF; the DOM boundary stub does not.
$('#v-text').value='# Notes\ntext\n';
assert.strictEqual(viewerHasDrafts(),false);
let sectionsRequested=false;
api=async url=>{sectionsRequested=url.startsWith('/api/sections');return {sections:[],sha256:'crlf',editable:true,sectionCount:0,totalChars:0};};
await openSections(); assert.strictEqual(sectionsRequested,true);
''')


if __name__ == "__main__":
    unittest.main()
