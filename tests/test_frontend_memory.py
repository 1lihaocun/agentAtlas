"""Memory-scope UI: real inline JS with the existing Node DOM boundary harness.

No browser/layout claims; the parent owns browser integration. Import the harness
module, not its TestCase, so unittest discovery never reruns inherited tests.
"""
import json
import shutil
import subprocess
import unittest

import test_frontend as frontend


FIXTURE = r'''
const levels=['user','profile','workspace','project','directory','session','unknown'];
const labels=['用户级','Profile 级','工作区级','项目级','目录级','会话级','未知'];
function memoryFile(level, i=0) {
  return {path:'/storage/'+level+i+'.md',name:level+i+'.md',category:'memory',
    platform:'hermes',profile:'work',scope:'global',project:'',bytes:10,mtime:1,
    editable:true,searchable:true,
    semantics:{level,label:labels[levels.indexOf(level)],appliesTo:level==='user'?[]:['/projects/app'],
      allProjects:level==='user',reader:'Hermes Agent',
      loading:{mode:'automatic',label:'自动加载',trigger:'会话开始时',portion:'固定记忆正文'},
      inheritance:'受 Profile 隔离；不按磁盘目录继承',
      basis:{kind:'documented_rule',label:'文档规则',detail:'官方规则；当前配置未验证',
        sources:[{title:'官方文档',url:'https://docs.example.test/memory'}]},
      observation:{state:'unverified',label:'未验证实际读取'},notes:['存放位置不等于作用域']}};
}
function memoryResponse(files=levels.map(level=>memoryFile(level))) {
  return {schemaVersion:1,files,projects:[{path:'/projects/app',name:'应用'}],
    platforms:['hermes'],profiles:['work'],
    counts:{total:files.length,matched:files.length,
      levels:Object.fromEntries(levels.map(level=>[level,files.filter(f=>f.semantics?.level===level).length]))},
    selected:{project:'',platform:'',profile:''},notice:'规则推算，不代表已实际读取'};
}
async function settle(){for(let i=0;i<12;i++) await Promise.resolve();}
DATA={files:[],roots:[],dupGroups:[],dupFiles:0};
ASSETS={files:[],totalFiles:0};
'''


@unittest.skipUnless(shutil.which("node"), "Node is required for extracted JS checks")
class MemoryFrontendTests(unittest.TestCase):
    def run_js(self, test):
        result = subprocess.run(
            ["node", "-e", frontend.HARNESS],
            input=json.dumps({"html": frontend.HTML, "script": frontend.SCRIPT,
                              "test": FIXTURE + test}),
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_navigation_fetches_separate_memory_catalog_and_groups_runtime_levels(self):
        self.run_js(r'''
assert(!requests.some(r=>r.url.startsWith('/api/memories')), 'memory catalog must load on demand');
renderSidebar();
assert($('#sidebar').innerHTML.includes('data-nav="memories"'), 'missing memory navigation');
assert($('#sidebar').innerHTML.includes('记忆作用域'));
const calls=[];
const response=memoryResponse(levels.slice().reverse().map(level=>memoryFile(level)));
api=async (url,opts)=>{calls.push({url,opts});return response;};
setView({type:'memories'}); await settle();
assert.strictEqual(calls.length,1); assert(calls[0].url.startsWith('/api/memories'));
assert(!calls[0].opts?.method || calls[0].opts.method==='GET');
assert.strictEqual($('#asset-tools').hidden,true);
assert.strictEqual($('#memory-tools').hidden,false);
assert.strictEqual($('#crumb-inner').textContent,'记忆作用域');
const html=$('#content').innerHTML;
let pos=-1;
for(const level of levels){
  const next=html.indexOf('data-memory-level="'+level+'"');
  assert(next>pos, 'missing or unordered level '+level); pos=next;
}
assert.strictEqual((html.match(/data-memory-count="1"/g)||[]).length,7);
assert($('#stats').innerHTML.includes('7'));
assert(html.includes('Hermes Agent')); assert(html.includes('自动加载'));
assert(html.includes('未验证实际读取'));
assert(!html.includes('dup-link')); assert(!html.includes('use-badge'));
assert.strictEqual(DATA.files.length,0); assert.strictEqual(ASSETS.files.length,0);
''')

    def test_filters_send_applicability_project_platform_profile_and_keep_global_entries(self):
        self.run_js(r'''
const calls=[], globalMemory=memoryFile('user');
const response=memoryResponse([globalMemory, memoryFile('project')]);
response.projects.push({path:'/projects/other & space',name:'其他 <项目>'});
response.platforms.push('claude'); response.profiles.push('client & work');
api=async url=>{calls.push(url);return response;};
setView({type:'memories'}); await settle();
assert($('#memory-project').innerHTML.includes('其他 &lt;项目&gt;'));
assert.strictEqual(typeof $('#memory-project').onchange,'function');
for(const [key,value] of Object.entries({project:'/projects/other & space',platform:'claude',profile:'client & work'})) {
  const el=$('#memory-'+key); el.value=value; await el.onchange({target:el});
}
const params=new URL(calls[calls.length-1],'http://localhost').searchParams;
assert.strictEqual(params.get('project'),'/projects/other & space');
assert.strictEqual(params.get('platform'),'claude');
assert.strictEqual(params.get('profile'),'client & work');
assert.strictEqual([...params.keys()].sort().join(','),'platform,profile,project');
assert($('#content').innerHTML.includes(globalMemory.name), 'backend global applicability must not be locally filtered by storage');
assert.strictEqual(assetFilters.project,'');
await $('#memory-reset').onclick();
const resetParams=new URL(calls[calls.length-1],'http://localhost').searchParams;
assert(![...resetParams.values()].some(Boolean));
assert.strictEqual($('#memory-project').value,'');
const before=calls.length; await $('#memory-refresh').onclick();
assert.strictEqual(calls.length,before+1);
assert(calls.every(url=>url.startsWith('/api/memories')));
''')

    def test_stale_memory_responses_cannot_restore_old_filters_or_departed_view(self):
        self.run_js(r'''
const pending=[]; api=url=>new Promise((resolve,reject)=>pending.push({url,resolve,reject}));
setView({type:'memories'});
const project=$('#memory-project'); project.value='/projects/new';
const changed=project.onchange({target:project});
const fresh=memoryResponse([memoryFile('project',2)]);
fresh.projects=[{path:'/projects/new',name:'new'}];
pending[1].resolve(fresh); await changed;
const good=$('#content').innerHTML;
pending[0].resolve(memoryResponse([memoryFile('user',9)])); await settle();
assert.strictEqual($('#content').innerHTML,good,'old query must not replace new results');
assert.strictEqual(project.value,'/projects/new');
const refreshing=$('#memory-refresh').onclick();
setView({type:'assets'}); const library=$('#content').innerHTML;
assert.strictEqual($('#memory-tools').hidden,true);
for(const key of ['project','platform','profile']) assert.strictEqual($('#memory-'+key).onchange,null);
assert.strictEqual($('#memory-refresh').onclick,null); assert.strictEqual($('#memory-reset').onclick,null);
assert.strictEqual($('#memory-status').textContent,'');
assert.strictEqual(MEMORIES,null);
pending[2].resolve(memoryResponse([memoryFile('user',77)])); await refreshing;
assert.strictEqual($('#content').innerHTML,library); assert.strictEqual(MEMORIES,null);
setView({type:'memories'}); setView({type:'dup'});
const duplicateView=$('#content').innerHTML;
pending[3].reject(new Error('obsolete failure')); await settle();
assert.strictEqual($('#content').innerHTML,duplicateView);
assert(!$('#memory-status').textContent.includes('obsolete'));
''')

    def test_memory_read_details_escape_metadata_and_only_link_http_sources(self):
        self.run_js(r'''
const f=memoryFile('directory');
f.path='/outside/project/<img src=x>\".md'; f.name='<svg onload=bad()> & note';
f.semantics.appliesTo=['/projects/<project>','/projects/second'];
f.semantics.reader='Agent <reader>';
f.semantics.loading={mode:'on_demand',label:'按需 <加载>',trigger:'打开 <文件> 后',portion:'指定 <片段>'};
f.semantics.inheritance='仅子目录 <条件>';
f.semantics.basis={kind:'path_inference',label:'路径推断',detail:'不是实际 <读取>',sources:[
  {title:'<官方> & 文档',url:'https://docs.example.test/?a=1&b=\"quoted\"'},
  {title:'unsafe script',url:'javascript:alert(1)'},
  {title:'unsafe html',url:'data:text/html,<script>bad</script>'},
  {title:'unsafe relative',url:'//evil.example.test'}]};
f.semantics.notes=['<img src=x onerror=bad()>','特殊 & 说明'];
api=async()=>memoryResponse([f,memoryFile('user')]);
setView({type:'memories'}); await settle();
const html=$('#content').innerHTML;
for(const text of ['打开 &lt;文件&gt; 后','指定 &lt;片段&gt;','仅子目录 &lt;条件&gt;',
  '不是实际 &lt;读取&gt;','/projects/&lt;project&gt;','/projects/second','&lt;官方&gt; &amp; 文档',
  '&lt;img src=x onerror=bad()&gt;','&lt;svg onload=bad()&gt; &amp; note','路径推断',
  '所选平台/Profile 内，不限项目（仍受读取条件约束）']) assert(html.includes(text),'missing detail '+text);
assert(!html.includes('<img')); assert(!html.includes('<svg')); assert(!html.includes('<script'));
assert(!/href="(?:javascript:|data:|\/\/)/i.test(html));
assert.strictEqual((html.match(/href=/g)||[]).length,2,'only the two safe official source links');
assert(html.includes('rel="noopener noreferrer"'));
assert(html.includes('未验证实际读取'));
''')

    def test_metadata_only_memories_are_visible_but_never_previewed_or_editable(self):
        self.run_js(r'''
const metadata={...memoryFile('profile'),name:'state.db',path:'/storage/state.db',searchable:false,editable:false};
const readonly={...memoryFile('project'),name:'readonly.md',editable:false};
api=async()=>memoryResponse([metadata,readonly]);
setView({type:'memories'}); await settle();
const html=$('#content').innerHTML;
assert(html.includes('state.db')); assert(html.includes('仅元数据'));
assert(!html.includes('data-asset-open="'+metadata.path+'"'));
assert(html.includes('只读预览'));
assert(html.includes('data-asset-open="'+readonly.path+'"'));
const libraryRow=assetRowHtml(metadata);
assert(!libraryRow.includes('file-row')); assert(!libraryRow.includes('data-asset-open'));
assert(libraryRow.includes('不预览')); assert(libraryRow.includes('仅元数据'));
const calls=[]; api=async url=>{calls.push(url);throw new Error('must not read');};
assert.strictEqual(await openFile(metadata.path),false);
assert.strictEqual(calls.length,0); assert($('#notice').textContent.includes('不预览'));
// Metadata discovered in the independent library must receive the same guard.
setView({type:'assets'}); ASSETS.files=[metadata];
assert.strictEqual(await openFile(metadata.path),false); assert.strictEqual(calls.length,0);
''')

    def test_viewer_uses_file_semantics_from_library_and_clears_to_honest_unknown(self):
        self.run_js(r'''
const f={...memoryFile('profile'),editable:false};
ASSETS.files=[f];
// No memory-view fetch: /api/file is authoritative even when opened from search.
const calls=[];
api=async url=>{calls.push(url);return {...f,content:'read-only memory',sha256:'v1'};};
await openFile(f.path,{startLine:1,endLine:1});
assert($('#v-memory') && !$('#v-memory').hidden,'memory viewer must expose semantics');
const detail=$('#v-memory-body').innerHTML;
for(const text of ['Profile 级','/projects/app','会话开始时','固定记忆正文','文档规则','未验证实际读取']) assert(detail.includes(text),text);
assert(detail.includes('https://docs.example.test/memory'));
assert.strictEqual($('#v-text').readOnly,true); assert.strictEqual($('#v-save').disabled,true);
assert.strictEqual($('#v-open').disabled,true); assert.strictEqual($('#v-effective').disabled,true);
assert(calls.every(url=>url.startsWith('/api/file?')));
// A semantics-free response must not borrow scopes from a path or prior file.
api=async()=>({path:'/project/.hermes/memories/MEMORY.md',content:'unknown',category:'memory',editable:true,sha256:'v2'});
await openFile('/project/.hermes/memories/MEMORY.md');
const unknown=$('#v-memory-body').innerHTML;
assert(unknown.includes('未知')); assert(unknown.includes('未验证实际读取'));
assert(!unknown.includes('不限项目')); assert(!unknown.includes('会话开始时'));
assert(!unknown.includes('/projects/app'));
api=async()=>({path:'/AGENTS.md',content:'instruction',category:'instruction',editable:true,sha256:'v3'});
await openFile('/AGENTS.md');
assert.strictEqual($('#v-memory').hidden,true); assert.strictEqual($('#v-memory-body').innerHTML,'');
assert.strictEqual($('#v-effective').disabled,false);
closeViewer(); assert.strictEqual($('#v-memory').hidden,true);
''')

    def test_catalog_job_refreshes_memory_rules_without_touching_editor_drafts(self):
        self.run_js(r'''
const file={...memoryFile('profile'),content:'original',sha256:'draft-base'};
let catalog=memoryResponse([file]);
const calls=[];
api=async url=>{
  calls.push(url);
  if(url.startsWith('/api/file?')) return file;
  if(url.startsWith('/api/memories')) return catalog;
  if(url==='/api/assets/status') return {job:{running:false},index:{}};
  if(url==='/api/assets') return {files:catalog.files,totalFiles:catalog.files.length};
  throw new Error('unexpected request '+url);
};
await openFile(file.path);
$('#v-text').value='unsaved raw draft'; $('#v-text').selectionStart=4; $('#v-text').scrollTop=37;
const sectionDraft={value:'unsaved section',defaultValue:'old'};
$('#v-sec').querySelectorAll=selector=>selector==='textarea'?[sectionDraft]:[];
setView({type:'memories'}); await settle();
const loaded=current, version=viewerVersion;
catalog=memoryResponse([file,memoryFile('session')]);
assetStatus={job:{running:true}};
await refreshAssetStatus();
assert.strictEqual(calls.filter(url=>url.startsWith('/api/memories')).length,2,'catalog completion must refresh memory rules');
assert($('#content').innerHTML.includes('session0.md'));
assert.strictEqual(current,loaded); assert.strictEqual(viewerVersion,version);
assert.strictEqual(current.sha256,'draft-base');
assert.strictEqual($('#v-text').value,'unsaved raw draft'); assert.strictEqual($('#v-text').selectionStart,4);
assert.strictEqual($('#v-text').scrollTop,37); assert.strictEqual(sectionDraft.value,'unsaved section');
assert.strictEqual(calls.filter(url=>url.startsWith('/api/file?')).length,1);
assert(!calls.some(url=>/embeddings|\/api\/search|\/api\/save/.test(url)));
// A catalog refresh started before a new filter cannot restart that query.
let finishAssets;
api=url=>url==='/api/assets'?new Promise(r=>finishAssets=r):Promise.resolve(memoryResponse([memoryFile('directory',55)]));
const assetReload=loadAssets();
const profile=$('#memory-profile'); profile.value='fresh-profile'; await profile.onchange({target:profile});
const newRules=MEMORIES;
finishAssets({files:catalog.files,totalFiles:catalog.files.length}); await assetReload;
assert.strictEqual(MEMORIES,newRules,'older catalog completion must not replace a newer filter request');
''')

    def test_memory_navigation_and_results_survive_instruction_map_failure(self):
        self.run_js(r'''
DATA=null; ASSETS=null; renderSidebar();
assert($('#sidebar').innerHTML.includes('data-nav="memories"'),'memory navigation must not depend on instruction map');
const response=memoryResponse([memoryFile('user'),memoryFile('profile')]); response.counts.total=9;
api=async url=>{if(url==='/api/tree') throw new Error('tree unavailable'); return response;};
setView({type:'memories'}); await settle();
assert(/data-nav="memories">[\s\S]*?nav-count">9</.test($('#sidebar').innerHTML),'sidebar uses whole memory count, not filtered files');
const content=$('#content').innerHTML;
await loadTree(); assert.strictEqual($('#content').innerHTML,content);
api=async()=>{throw new Error('memory endpoint unavailable <retry>');};
await $('#memory-refresh').onclick();
assert($('#memory-status').textContent.includes('memory endpoint unavailable'));
assert($('#content').innerHTML.includes('不能由存放位置判断'));
assert.strictEqual($('#stats').innerHTML,'');
assert(!$('#content').innerHTML.includes('user0.md'),'failed refresh must not present old rules as current');
''')

    def test_invalid_or_inconsistent_memory_schema_is_unknown_not_an_empty_success(self):
        self.run_js(r'''
api=async()=>({...memoryResponse(),schemaVersion:2});
setView({type:'memories'}); await settle();
assert.strictEqual(MEMORIES,null,'unsupported response must not be presented as verified rule metadata');
assert($('#memory-status').textContent.includes('格式'));
const bad=memoryResponse(); bad.counts.matched=99;
api=async()=>bad; await $('#memory-refresh').onclick();
assert.strictEqual(MEMORIES,null); assert($('#memory-status').textContent.includes('数量'));
const missing=memoryFile('unknown'); delete missing.semantics;
const response=memoryResponse([missing]); response.counts.levels.unknown=1;
api=async()=>response; await $('#memory-refresh').onclick();
assert($('#content').innerHTML.includes('data-memory-level="unknown"'));
assert($('#content').innerHTML.includes('未确认适用项目'));
assert(!$('#content').innerHTML.includes('不限项目'));
api=async()=>memoryResponse([]); await $('#memory-refresh').onclick();
assert($('#content').innerHTML.includes('没有匹配的记忆'));
assert($('#stats').innerHTML.includes('<b>0</b>'));
''')

    def test_memory_classification_never_enables_instruction_stack_even_with_old_map_entry(self):
        self.run_js(r'''
const f=memoryFile('project');
DATA.files=[{path:f.path,name:f.name,kind:'other',scope:'user',sha:'old-map',dup:true}];
api=async()=>({...f,content:'memory',sha256:'m1'});
await openFile(f.path);
assert.strictEqual($('#v-effective').disabled,true,'memory is not an inherited instruction');
const calls=[]; api=async url=>{calls.push(url);return {};};
await $('#v-effective').listeners.click();
assert.strictEqual(calls.length,0,'handler must also prevent instruction stack lookup for memory');
assert(!$('#v-tags').innerHTML.includes('重复副本'));
assert(!assetRowHtml(f).includes('dup-link')); assert(!memoryEntryHtml(f).includes('use-badge'));
''')

    def test_settings_disclose_batch_stop_without_claiming_inflight_recall(self):
        self.assertTrue("保存或清除设置会停止后续上传批次" in frontend.HTML, "missing batch-stop disclosure")
        self.assertTrue("已发出的请求无法撤回" in frontend.HTML, "missing in-flight limit disclosure")

    def test_unrecognized_levels_stay_visible_and_group_counts_must_close(self):
        self.run_js(r'''
const f=memoryFile('unknown'); f.semantics.level='unrecognized-storage-folder';
const response=memoryResponse([f]); response.counts.levels.unknown=1;
api=async()=>response;
setView({type:'memories'}); await settle();
assert($('#content').innerHTML.includes('data-memory-level="unknown"'),'unknown levels cannot silently drop a file');
assert($('#content').innerHTML.includes(f.name));
response.counts.levels.unknown=2;
await $('#memory-refresh').onclick();
assert.strictEqual(MEMORIES,null,'reported level counts must match displayed groups');
assert($('#memory-status').textContent.includes('数量'));
''')


if __name__ == "__main__":
    unittest.main()
