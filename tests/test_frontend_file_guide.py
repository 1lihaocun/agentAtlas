"""File guide UI contracts against the real inline JS, not a parallel renderer.

DOM/fetch boundaries are stubbed; this does not claim browser layout coverage.
Run: python3 -m unittest discover -s tests -p 'test_frontend_file_guide.py' -v
"""
import json
import shutil
import subprocess
import unittest

try:
    from . import test_frontend as frontend
except ImportError:
    import test_frontend as frontend

# Keep the shared harness unchanged; capture document handlers for real delegation.
HARNESS = frontend.HARNESS.replace(
    "addEventListener(){}, removeEventListener(){}, createElement:element, body:element(), activeElement:null",
    "listeners:{}, addEventListener(t,f,c){(this.listeners[t] ||= []).push({fn:f,capture:c});}, "
    "removeEventListener(){}, createElement:element, body:element(), activeElement:null",
)
FIXTURE = r'''
const activity = {id:'activity', label:'活动摘要', role:'记忆素材', purpose:'记录一段活动，不是最终记忆。',
  producer:'后台摘要流程', consumer:'后台整理的候选输入', cautions:['不证明当前启用'],
  evidence:'本机声明 / 路径匹配', reviewedAt:'2026-09-23', notice:'设计用途不证明已读取。',
  matchPatterns:['~/.codex/memories/extensions/skysight/resources/*.md'],
  sources:[{kind:'本机声明',label:'布局 <img src=x>',path:'~/instructions<bad>.md',line:3,checkedAt:'2026-09-23'},
    {kind:'官方文档',label:'安全来源',url:'https://example.test/docs?q=1&x=2'},
    {label:'脚本链接',url:'javascript:alert(1)'}, {label:'凭证链接',url:'https://user:secret@example.test/'},
    {label:'本地链接',url:'file:///private/x'}, {label:'相对链接',url:'/docs'}],
  filename:{original:null,status:'template',pattern:'YYYY-MM-DDTHH-MM-SS-{4字母}-{10min或6h}-{描述}.md',
    fields:[{key:'window',label:'摘要粒度',meaning:'10min 是十分钟级，不是 TTL。'},
      {key:'identifier',label:'短标识',meaning:'保留大小写，算法未知。'}],
    examples:['2026-09-16T00-10-00-CHvw-10min-memory-summary.md'],cautions:['未指定时区']}};
const memory = {...activity,id:'memory',label:'正式记忆',role:'正式记忆',purpose:'保存稳定约定',sources:[],
  filename:{original:null,status:'template',pattern:'MEMORY.md',fields:[],cautions:[]}};
const library = {schemaVersion:1,title:'文件类型说明库',notice:'设计说明≠本次实际读取',reviewedAt:'2026-09-23',
  maintenancePath:'docs/file-types.json',types:[activity,memory]};
const calls=[];
fetch=async (url,opts)=>{calls.push({url,opts});return {ok:true,status:200,json:async()=>({ok:true,data:library})};};
async function guideClick(selector, dataset={}) {
  const handlers=document.listeners.click.filter(h=>h.capture===true);
  assert.strictEqual(handlers.length,1,'only one document capture click delegate');
  let stopped=false, prevented=false;
  const target={dataset,closest:s=>s===selector?target:null};
  await handlers[0].fn({target,stopPropagation(){stopped=true;},preventDefault(){prevented=true;}});
  assert(stopped,'guide actions preempt row opening');
}
'''


@unittest.skipUnless(shutil.which('node'), 'Node is required for inline JS behavior checks')
class FileGuideFrontendTests(unittest.TestCase):
    def run_js(self, test):
        result = subprocess.run(
            ['node', '-e', HARNESS],
            input=json.dumps({'html': frontend.HTML, 'script': frontend.SCRIPT,
                              'test': FIXTURE + test + '\nconsole.log("FILE_GUIDE_TEST_COMPLETE");'}),
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('FILE_GUIDE_TEST_COMPLETE', result.stdout, 'Unsettled async test: ' + result.stderr)

    def test_library_dialog_has_local_search_and_complete_safe_details(self):
        self.run_js(r'''
assert($('#btn-file-guide'),'top-level 文件说明 entry exists');
await $('#btn-file-guide').listeners.click();
assert.strictEqual($('#file-guide-dialog').open,true);
assert.deepStrictEqual(calls.map(c=>c.url),['/api/file-guide']);
assert($('#file-guide-list').innerHTML.includes('活动摘要'));
assert($('#file-guide-list').innerHTML.includes('正式记忆'));
const html=$('#file-guide-detail').innerHTML;
for(const text of ['用途','记忆素材','后台摘要流程','后台整理的候选输入','路径约定','resources/*.md',
  '命名字段','摘要粒度','window','不是 TTL','未指定时区','不证明当前启用','本机声明 / 路径匹配',
  '复核','2026-09-23','2026-09-16T00-10-00-CHvw-10min-memory-summary.md']) assert(html.includes(text),text);
assert($('#file-guide-notice').textContent.includes('设计说明'));
assert($('#file-guide-meta').textContent.includes('docs/file-types.json'));
assert(html.includes('布局 &lt;img src=x&gt;')); assert(html.includes('instructions&lt;bad&gt;.md'));
assert(html.includes('href="https://example.test/docs?q=1&amp;x=2"'));
for(const bad of ['<img','href="javascript:','href="file:','href="/docs','user:secret']) assert(!html.includes(bad),bad);
$('#file-guide-search').value=' TTL  ';
$('#file-guide-search').listeners.input();
assert($('#file-guide-list').innerHTML.includes('活动摘要'));
assert(!$('#file-guide-list').innerHTML.includes('data-guide-type="memory"'));
assert.strictEqual(calls.length,1,'search is local, no cloud or content requests');
$('#file-guide-search').value='no such type'; $('#file-guide-search').listeners.input();
assert($('#file-guide-list').innerHTML.includes('没有匹配'));
$('#file-guide-search').value=''; $('#file-guide-search').listeners.input();
await guideClick('[data-guide-type]',{guideType:'memory'});
assert($('#file-guide-detail').innerHTML.includes('保存稳定约定'));
assert.strictEqual(calls.length,1,'changing cached type does not fetch source text');
''')

    def test_metadata_and_file_rows_open_only_guide_with_exact_filename_parts(self):
        self.run_js(r'''
const path='/memory/<odd>"/2026-09-16T00-10-00-CHvw-10min-memory-summary.md';
const file={path,name:'Activity <bad>',category:'memory',platform:'codex',editable:false,
  searchable:false,memoryInfo:{status:'metadata_only'},bytes:9};
for(const html of [memoryEntryHtml(file),assetRowHtml(file),
  assetRowHtml({...file,category:'log',searchable:true}),fileRow({...file,kind:'agents'})]) {
  assert(html.includes('data-guide-path="'+esc(path)+'"'),'every row has a guide action');
  assert(html.includes('用途/命名')); assert(!html.includes('<odd>'));
}
assert(!memoryEntryHtml(file).includes('data-asset-open'));
assert(!assetRowHtml(file).includes('class="file-row"'));
MEMORIES={files:[file]};
current={path:'/draft.md',category:'memory',editable:true,content:'saved',sha256:'base'};
$('#v-text').value='unsaved'; $('#v-text').scrollTop=35;
view={type:'memories',mode:'directory',directory:'/memory'};
memoryFilters.project='/project'; memoryLocal.query='keep'; memoryPage=3; memoryRecursive=true;
assetFilters.category='memory'; assetPage=2;
const before=JSON.stringify({view,memoryFilters,memoryLocal,memoryPage,memoryRecursive,assetFilters,assetPage});
const specific={...activity,path,filename:{...activity.filename,original:path.split('/').pop(),status:'matched',
  fields:[{key:'timestamp',label:'时间标记',value:'2026-09-16T00-10-00',meaning:'不指定时区'},
    {key:'identifier',label:'短标识',value:'CHvw',meaning:'保留大小写'},
    {key:'window',label:'摘要粒度',value:'10min',meaning:'十分钟级，不是 TTL'},
    {key:'slug',label:'描述短名',value:'memory-summary',meaning:'通用短名，不是正文主题'},
    {key:'extension',label:'格式',value:'.md',meaning:'Markdown'}]}};
fetch=async (url,opts)=>{calls.push({url,opts});return {ok:true,json:async()=>({data:url==='/api/file-guide'?library:specific})};};
bindRows(); await guideClick('[data-guide-path]',{guidePath:path});
assert(calls.some(c=>c.url==='/api/file-guide?path='+encodeURIComponent(path)));
assert(calls.every(c=>c.url.startsWith('/api/file-guide')&&!c.opts?.method));
const html=$('#file-guide-detail').innerHTML;
for(const text of ['当前文件','逐段','CHvw','10min','memory-summary','.md','时间标记',esc(path)]) assert(html.includes(text),text);
assert(!html.includes('<odd>')); assert(html.includes('已匹配'));
assert.strictEqual($('#v-text').value,'unsaved'); assert.strictEqual($('#v-text').scrollTop,35);
assert.strictEqual(JSON.stringify({view,memoryFilters,memoryLocal,memoryPage,memoryRecursive,assetFilters,assetPage}),before);
const unknown={id:'unknown',label:'尚未收录的文件类型',purpose:'不由文件名猜测读取规则。',path:'/unknown',
  filename:{original:'<unknown>.dat',status:'unknown',fields:[],cautions:[]}};
fetch=async url=>({ok:true,json:async()=>({data:url==='/api/file-guide'?library:unknown})});
await guideClick('[data-guide-path]',{guidePath:'/unknown'});
assert($('#file-guide-detail').innerHTML.includes('尚未收录'));
assert($('#file-guide-detail').innerHTML.includes('&lt;unknown&gt;.dat'));
assert($('#file-guide-detail').innerHTML.includes('未匹配'));
assert(!$('#file-guide-detail').innerHTML.includes('undefined'));
''')

    def test_late_requests_never_replace_new_path_type_or_reopened_dialog(self):
        self.run_js(r'''
await $('#btn-file-guide').listeners.click();
const deferred={};
api=(url,opts)=>new Promise((resolve,reject)=>{deferred[url]={resolve,reject,opts};});
const a=openFileGuide('/a'), b=openFileGuide('/b');
assert(!$('#file-guide-detail').innerHTML.includes('后台摘要流程'),'new path clears old detail while loading');
deferred['/api/file-guide?path=%2Fb'].resolve({...activity,path:'/b',label:'CURRENT B'}); await b;
deferred['/api/file-guide?path=%2Fa'].resolve({...activity,path:'/a',label:'STALE A'}); await a;
assert($('#file-guide-detail').innerHTML.includes('CURRENT B'));
assert(!$('#file-guide-detail').innerHTML.includes('STALE A'));
const c=openFileGuide('/c');
await guideClick('[data-guide-type]',{guideType:'memory'});
const selected=$('#file-guide-detail').innerHTML;
deferred['/api/file-guide?path=%2Fc'].reject(new Error('STALE ERROR')); await c;
assert.strictEqual($('#file-guide-detail').innerHTML,selected);
assert(!$('#file-guide-status').textContent.includes('STALE ERROR'));
const closed=openFileGuide('/closed');
$('#file-guide-close').listeners.click();
assert.strictEqual($('#file-guide-dialog').open,false);
api=async()=>library;
await $('#btn-file-guide').listeners.click();
const reopened=$('#file-guide-detail').innerHTML;
deferred['/api/file-guide?path=%2Fclosed'].resolve({...activity,path:'/closed',label:'STALE CLOSED'}); await closed;
assert.strictEqual($('#file-guide-detail').innerHTML,reopened);
assert.strictEqual($('#file-guide-dialog').open,true);
// Native dialog.close emits its close event asynchronously; it must not cancel a newer opening.
$('#file-guide-dialog').listeners.close();
assert.strictEqual($('#file-guide-dialog').open,true);
assert.strictEqual($('#file-guide-detail').innerHTML,reopened);
''')

    def test_refresh_and_retry_preserve_selected_page_path_query_and_scroll(self):
        self.run_js(r'''
assert($('#file-guide-refresh'),'refresh/retry control exists');
await $('#btn-file-guide').listeners.click();
await guideClick('[data-guide-type]',{guideType:'memory'});
$('#file-guide-search').value='正式'; $('#file-guide-search').listeners.input();
$('#file-guide-detail').scrollTop=120;
const fresh={...library,types:[activity,{...memory,purpose:'更新后的正式记忆说明'}]};
api=async url=>{calls.push({url});return fresh;};
await $('#file-guide-refresh').listeners.click();
assert($('#file-guide-detail').innerHTML.includes('更新后的正式记忆说明'));
assert.strictEqual($('#file-guide-search').value,'正式');
assert.strictEqual($('#file-guide-detail').scrollTop,120);
const path='/specific.md';
api=async url=>url==='/api/file-guide'?fresh:{...activity,path,filename:{...activity.filename,original:'specific.md',status:'unknown'}};
await openFileGuide(path); $('#file-guide-detail').scrollTop=77;
api=async url=>{calls.push({url});throw new Error('<offline>');};
await $('#file-guide-refresh').listeners.click();
assert($('#file-guide-status').textContent.includes('<offline>'));
assert($('#file-guide-status').textContent.includes('重试'));
assert($('#file-guide-detail').innerHTML.includes(path),'failed refresh retains current page');
api=async url=>{calls.push({url});return url==='/api/file-guide'?fresh:{...memory,path,label:'回读成功',filename:{original:'specific.md',status:'unknown'}};};
await $('#file-guide-refresh').listeners.click();
assert($('#file-guide-detail').innerHTML.includes('回读成功'));
assert($('#file-guide-detail').innerHTML.includes(path));
assert.strictEqual(calls[calls.length-1].url,'/api/file-guide?path='+encodeURIComponent(path));
assert.strictEqual($('#file-guide-detail').scrollTop,77);
assert.strictEqual($('#file-guide-status').textContent,'');
// Refreshing a library must not silently jump to its first type if the selected page was removed.
await guideClick('[data-guide-type]',{guideType:'memory'});
api=async()=>({...library,types:[activity]});
await $('#file-guide-refresh').listeners.click();
assert($('#file-guide-detail').innerHTML.includes('不在当前说明库'));
assert(!$('#file-guide-detail').innerHTML.includes('后台摘要流程'));
''')

    def test_missing_endpoint_explains_backend_restart_and_recovers_without_reload(self):
        self.run_js(r'''
window.location={origin:'http://127.0.0.1:7788'};
current={path:'/draft.md',content:'saved',editable:true,sha256:'base'};
$('#v-text').value='unsaved draft';
fetch=async (url,opts)=>{calls.push({url,opts});return {ok:false,status:404,json:async()=>({ok:false,error:'unknown endpoint'})};};
await $('#btn-file-guide').listeners.click();
const status=$('#file-guide-status').textContent;
assert(status.includes('后端') && status.includes('重启'),status);
assert(status.includes('http://127.0.0.1:7788'),status);
assert(status.includes('仅刷新页面'),status);
assert(!status.includes('unknown endpoint'),status);
assert.strictEqual($('#v-text').value,'unsaved draft');
fetch=async (url,opts)=>{calls.push({url,opts});return {ok:true,status:200,json:async()=>({ok:true,data:library})};};
await $('#file-guide-refresh').listeners.click();
assert.strictEqual($('#file-guide-status').textContent,'');
assert($('#file-guide-detail').innerHTML.includes('活动摘要'));
assert(calls.every(c=>c.url==='/api/file-guide' && !c.opts?.method));
assert.strictEqual($('#v-text').value,'unsaved draft');
// A missing catalog path is not evidence of an old server.
fetch=async()=>({ok:false,status:404,json:async()=>({ok:false,error:'文件不存在'})});
await openFileGuide('/missing.md');
assert($('#file-guide-status').textContent.includes('文件不存在'));
assert(!$('#file-guide-status').textContent.includes('重启'));
''')

    def test_escape_and_shortcuts_do_not_touch_background_drafts_or_navigation(self):
        self.run_js(r'''
current={path:'/draft.md',category:'memory',editable:true,content:'saved',sha256:'base'};
$('#v-text').value='raw draft'; $('#v-text').scrollTop=42; $('#v-text').setSelectionRange(2,5);
const section={value:'section draft',defaultValue:'saved section'};
$('#v-sec').querySelectorAll=()=>[section]; secMode=true;
$('#viewer').classList.add('open'); hist=[{type:'assets'}];
view={type:'memories',mode:'directory',directory:'/memory'};
confirm=()=>{throw new Error('guide must not ask to discard source drafts');};
let saves=0, backs=0; saveFile=()=>{saves++;}; goBack=()=>{backs++;};
await $('#btn-file-guide').listeners.click();
for(const key of ['s','[','Escape']) {
  const e={key,metaKey:key!=='Escape',preventDefault(){}};
  for(const h of document.listeners.keydown) h.fn(e);
}
assert.strictEqual(saves,0); assert.strictEqual(backs,0);
assert.strictEqual($('#viewer').classList.contains('open'),true);
assert.strictEqual($('#file-guide-dialog').open,true,'native cancel handles Escape');
let finish; api=()=>new Promise(resolve=>finish=resolve);
const pending=openFileGuide('/pending');
let prevented=false;
$('#file-guide-dialog').listeners.cancel({preventDefault(){prevented=true;}});
assert(prevented); assert.strictEqual($('#file-guide-dialog').open,false);
const closed=$('#file-guide-detail').innerHTML;
finish({...activity,path:'/pending',label:'STALE ESCAPE'}); await pending;
assert.strictEqual($('#file-guide-detail').innerHTML,closed);
assert.strictEqual(current.path,'/draft.md'); assert.strictEqual($('#v-text').value,'raw draft');
assert.strictEqual(section.value,'section draft'); assert.strictEqual(secMode,true);
assert.strictEqual($('#v-text').scrollTop,42); assert.strictEqual($('#v-text').selectionStart,2);
assert.strictEqual(view.directory,'/memory');
''')

    def test_viewer_summary_uses_attached_guide_without_changing_access_or_drafts(self):
        self.run_js(r'''
assert($('#v-file-guide'),'compact viewer guide exists');
const source={path:'/log.jsonl',content:'source text',category:'log',editable:false,sha256:'hash',
  fileGuide:{...activity,label:'活动 <unsafe>',purpose:'用途 <script>bad</script>'}};
api=async url=>{calls.push({url});return url==='/api/file-guide'?library:url.startsWith('/api/file-guide?')?{...activity,path:source.path}:source;};
await openFile(source.path);
assert.strictEqual(calls.length,1,'viewer does not auto-fetch explanations or external sources');
assert.strictEqual($('#v-file-guide').hidden,false);
assert($('#v-file-guide-summary').textContent.includes('活动 <unsafe>'));
assert($('#v-file-guide-summary').textContent.includes('用途 <script>bad</script>'));
assert($('#v-file-guide-summary').textContent.includes('设计'));
assert.strictEqual($('#v-file-guide-summary').innerHTML,'','summary uses textContent, not unescaped HTML');
assert.strictEqual($('#v-file-guide-open').disabled,false,'read-only files still get explanations');
await $('#v-file-guide-open').listeners.click();
assert($('#file-guide-detail').innerHTML.includes(source.path));
assert.strictEqual($('#v-save').disabled,true); assert.strictEqual($('#v-open').disabled,true);
assert.strictEqual($('#v-translate').disabled,true); assert.strictEqual($('#v-text').readOnly,true);
assert.strictEqual($('#v-text').value,'source text');
$('#file-guide-close').listeners.click();
const missing={path:'/memory.md',content:'saved',category:'memory',editable:true,sha256:'next',fileGuideError:'registry <broken>'};
api=async()=>missing; await openFile(missing.path);
assert($('#v-file-guide-summary').textContent.includes('registry <broken>'));
assert($('#v-file-guide-summary').textContent.includes('暂不可用'));
assert.strictEqual($('#v-text').value,'saved'); assert.strictEqual($('#v-save').disabled,false);
$('#v-text').value='unsaved draft'; $('#v-text').setSelectionRange(3,5); $('#v-text').scrollTop=20;
api=async url=>url==='/api/file-guide'?library:{...memory,path:missing.path};
await $('#v-file-guide-open').listeners.click();
assert.strictEqual($('#v-text').value,'unsaved draft'); assert.strictEqual($('#v-text').selectionStart,3);
assert.strictEqual($('#v-text').scrollTop,20); assert.strictEqual(current.sha256,'next');
$('#file-guide-close').listeners.click(); confirm=()=>true;
let finish; api=()=>new Promise(r=>finish=r);
const slow=openFile('/old.md');
assert.strictEqual($('#v-file-guide-open').disabled,true);
api=async()=>({...missing,path:'/new.md',fileGuide:{...memory,label:'NEW GUIDE'}});
await openFile('/new.md');
finish({...missing,path:'/old.md',fileGuide:{...activity,label:'OLD GUIDE'}}); await slow;
assert($('#v-file-guide-summary').textContent.includes('NEW GUIDE'));
assert(!$('#v-file-guide-summary').textContent.includes('OLD GUIDE'));
''')

    def test_unavailable_rows_keep_access_warning_and_guide_discloses_no_source_check(self):
        self.run_js(r'''
const file={path:'/missing.md',name:'missing.md',category:'memory',searchable:false,editable:false,
  memoryInfo:{status:'unavailable',reason:'文件已不存在'}};
for(const html of [memoryEntryHtml(file),assetRowHtml(file)]) {
  assert(html.includes('源文件不可用')); assert(html.includes('文件已不存在'));
  assert(html.includes('data-guide-path="/missing.md"')); assert(!html.includes('data-asset-open'));
}
const description={...activity,path:file.path,sources:[{kind:'本机声明',label:'声明位置',path:'~/instructions.md',line:64,checkedAt:'2026-09-22'}]};
api=async url=>{calls.push({url});return url==='/api/file-guide'?library:description;};
bindRows(); await guideClick('[data-guide-path]',{guidePath:file.path});
assert($('#file-guide-notice').textContent.includes('不核验源文件是否存在'));
assert($('#file-guide-detail').innerHTML.includes('清单路径'));
assert($('#file-guide-detail').innerHTML.includes(' L64'));
assert($('#file-guide-detail').innerHTML.includes('复核 2026-09-22'));
assert(calls.every(c=>c.url.startsWith('/api/file-guide')));
assert.strictEqual(current,null);
''')

    def test_bad_library_and_mismatched_file_response_fail_without_reading_source(self):
        self.run_js(r'''
fetch=async(url,opts)=>{calls.push({url,opts});return {ok:true,json:async()=>({data:{schemaVersion:99,types:[]}})};};
await $('#btn-file-guide').listeners.click();
assert($('#file-guide-status').textContent.includes('格式未知'));
assert($('#file-guide-status').textContent.includes('重试'));
fetch=async(url,opts)=>{calls.push({url,opts});return {ok:true,json:async()=>({data:library})};};
await $('#file-guide-refresh').listeners.click();
assert($('#file-guide-detail').innerHTML.includes('活动摘要'));
fetch=async(url,opts)=>{calls.push({url,opts});return {ok:true,json:async()=>({data:{...activity,path:'/wrong.md',label:'WRONG FILE'}})};};
await openFileGuide('/selected.md');
assert(!$('#file-guide-detail').innerHTML.includes('WRONG FILE'));
assert($('#file-guide-status').textContent.includes('不一致'));
assert($('#file-guide-detail').innerHTML.includes('/selected.md'));
fetch=async(url,opts)=>{calls.push({url,opts});return {ok:false,status:403,json:async()=>({ok:false,error:'路径未收录'})};};
await openFileGuide('/forbidden');
assert($('#file-guide-status').textContent.includes('路径未收录'));
assert(calls.every(c=>c.url.startsWith('/api/file-guide')));
''')

    def test_generation_mechanism_block_and_provenance_evidence_panel(self):
        self.run_js(r'''
library.types[0] = {id:'codex.rollout.summary', label:'Codex 会话 / 任务回顾', role:'会话摘要',
  purpose:'x', producer:'p', consumer:'c', generationMechanism:'由后台 stage1 生成。',
  docs:['https://developers.openai.com/codex/customization/memories'],
  evidence:'e', reviewedAt:'2026-09-23', notice:'n',
  matchPatterns:[], sources:[], path:'/x.md',
  filename:{original:null,status:'template',pattern:'',fields:[],cautions:[]}};
const realFetch = fetch;
fetch=async (url,opts)=>{
  if(url.indexOf('/api/provenance')===0) return {ok:true,status:200,json:async()=>({ok:true,data:
    {path:'/x.md',label:'Codex 会话 / 任务回顾',mechanism:'后台 stage1 任务生成',
     docs:['https://developers.openai.com/codex/customization/memories'],
     evidence:[{kind:'任务记录',label:'jobs 表',records:[{kind:'memory_stage1',status:'done'}]}],
     note:'后台任务调用模型时的确切提示词不落盘。'}})};
  return realFetch(url,opts);};
await $('#btn-file-guide').listeners.click();
const html=$('#file-guide-detail').innerHTML;
for(const text of ['生成机制','由后台 stage1 生成','查看生成证据','官方文档','developers.openai.com'])
  assert(html.includes(text),text);
assert(html.includes('data-guide-provenance="/x.md"'),'provenance button uses current file path');
let stopped=false;
const btn={dataset:{guideProvenance:'/x.md'},closest:s=>s==='[data-guide-provenance]'?btn:null};
const handlers=document.listeners.click.filter(h=>h.capture===true);
assert.strictEqual(handlers.length,1,'provenance reuses the single capture delegate');
await handlers[0].fn({target:btn,stopPropagation(){stopped=true;},preventDefault(){}});
assert(stopped,'provenance click does not open the file row');
const panel=$('#file-guide-detail').innerHTML;
for(const text of ['生成机制 ·','机制：','后台 stage1 任务生成','官方文档','不落盘','本机只读取证','memory_stage1'])
  assert(panel.includes(text),text);
''')


if __name__ == '__main__':
    unittest.main()
