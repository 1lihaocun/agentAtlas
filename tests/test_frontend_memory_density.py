"""Memory list density and local controls at the existing Node DOM boundary."""
import json
import shutil
import subprocess
import unittest

import test_frontend as frontend
import test_frontend_memory as memory


@unittest.skipUnless(shutil.which("node"), "Node is required for extracted JS checks")
class MemoryDensityTests(unittest.TestCase):
    def run_js(self, test):
        result = subprocess.run(
            ["node", "-e", frontend.HARNESS],
            input=json.dumps({"html": frontend.HTML, "script": frontend.SCRIPT,
                              "test": memory.FIXTURE + test}),
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pagination_caps_whole_view_and_preserves_group_totals(self):
        self.run_js(r'''
const files=[...Array.from({length:30},(_,i)=>memoryFile('user',i)),
  ...Array.from({length:40},(_,i)=>memoryFile('project',i)),
  ...Array.from({length:51},(_,i)=>memoryFile('unknown',i))];
const calls=[]; api=async url=>{calls.push(url);return memoryResponse(files.slice().reverse());};
setView({type:'memories'}); await settle();
const rows=()=>[...$('#content').innerHTML.matchAll(/data-asset-open="([^"]+)"/g)].map(m=>m[1]);
assert.strictEqual(rows().length,50,'page cap applies across all levels, not per group');
assert($('#content').innerHTML.indexOf('data-memory-level="user"') < $('#content').innerHTML.indexOf('data-memory-level="project"'));
assert(/data-memory-count="40"[^>]*data-memory-shown="20"/.test($('#content').innerHTML),'group total must not be mistaken for page subset');
assert($('#memory-page-status').textContent.includes('1–50'));
assert.strictEqual($('#memory-prev').disabled,true);
const seen=new Set(rows());
await $('#memory-next').onclick();
assert.strictEqual(rows().length,50); rows().forEach(p=>{assert(!seen.has(p));seen.add(p);});
assert(/data-memory-count="40"[^>]*data-memory-shown="20"/.test($('#content').innerHTML));
await $('#memory-next').onclick();
assert.strictEqual(rows().length,21); rows().forEach(p=>{assert(!seen.has(p));seen.add(p);});
assert.strictEqual(seen.size,121); assert.strictEqual($('#memory-next').disabled,true);
assert($('#memory-page-status').textContent.includes('101–121'));
await $('#memory-prev').onclick(); assert.strictEqual(rows().length,50);
assert.strictEqual(calls.length,1,'paging is local and never fetches file bodies');
''')

    def test_level_filter_reaches_small_profile_group_without_paging_through_archive(self):
        self.run_js(r'''
const files=[...Array.from({length:140},(_,i)=>memoryFile('user',i)),memoryFile('profile')];
const calls=[];api=async url=>{calls.push(url);return memoryResponse(files);};
setView({type:'memories'});await settle();
const level=$('#memory-level');
assert.strictEqual(typeof level?.onchange,'function','level selector must reach a small hidden group');
level.value='profile';await level.onchange({target:level});
assert($('#content').innerHTML.includes('profile0.md'));
assert(!$('#content').innerHTML.includes('user0.md'));
assert($('#memory-page-status').textContent.includes('匹配 1'));
assert.strictEqual(calls.length,1);
await $('#memory-reset').onclick();assert.strictEqual(level.value,'');
setView({type:'assets'});assert.strictEqual(level.onchange,null);
''')

    def test_local_metadata_search_and_stage_do_not_change_api_contract(self):
        self.run_js(r'''
const files=Array.from({length:64},(_,i)=>memoryFile('unknown',i));
files[0].memoryInfo={title:'Unique Architecture',relatedProjects:[{path:'/repos/sky',name:'SkySight',basis:'content',evidence:{path:'/proof.md',line:8}}]};
files[0].semantics.pipeline={stage:'background'};
files[1].semantics.pipeline={stage:'foreground'};
files[2].semantics.pipeline={stage:'unrecognized'};
for(const f of files) { f.content='body-only secret'; f.snippet='snippet-only'; }
const calls=[]; api=async url=>{calls.push(url);return memoryResponse(files);};
setView({type:'memories'}); await settle();
assert($('#memory-search'),'local metadata search must exist independently of global/cloud search');
assert($('#memory-stage'),'pipeline stage selector is missing');
const query=async value=>{const el=$('#memory-search');el.value=value;await el.oninput({target:el});};
const stage=async value=>{const el=$('#memory-stage');el.value=value;await el.onchange({target:el});};
await $('#memory-next').onclick();
await query('  ARCHITECTURE   skysight ');
assert($('#memory-page-status').textContent.includes('1–1 / 匹配 1'));
assert(/data-memory-count="64"[^>]*data-memory-shown="1"[^>]*data-memory-matched="1"/.test($('#content').innerHTML));
assert($('#stats').innerHTML.includes('<b>1</b>'));
assert($('#content').innerHTML.includes('unknown0.md')); assert(!$('#content').innerHTML.includes('unknown1.md'));
await stage('foreground'); assert($('#content').innerHTML.includes('没有匹配的记忆'));
assert($('#memory-page-status').textContent.includes('0–0'));
await query(''); assert($('#content').innerHTML.includes('unknown1.md'));
await stage('background'); assert($('#content').innerHTML.includes('unknown0.md'));
await stage('unknown'); assert($('#memory-page-status').textContent.includes('匹配 62'));
assert(!$('#content').innerHTML.includes('unknown0.md')); assert(!$('#content').innerHTML.includes('unknown1.md'));
await stage('');
for(const q of ['body-only','snippet-only']) {await query(q);assert($('#content').innerHTML.includes('没有匹配的记忆'));}
await query('/storage/unknown63.md'); assert($('#content').innerHTML.includes('unknown63.md'));
assert.strictEqual(calls.length,1,'local filters must not call any endpoint');
const platform=$('#memory-platform');platform.value='hermes';await platform.onchange({target:platform});
assert.strictEqual($('#memory-search').value,'/storage/unknown63.md');
assert.strictEqual([...new URL(calls[1],'http://localhost').searchParams.keys()].sort().join(','),'platform,profile,project');
await $('#memory-reset').onclick();
assert.strictEqual($('#memory-search').value,'');assert.strictEqual($('#memory-stage').value,'');
assert($('#memory-page-status').textContent.includes('1–50'));
''')

    def test_equal_totals_are_not_repeated_as_multiple_stat_cards(self):
        self.run_js(r'''
api=async()=>memoryResponse([memoryFile('user'),memoryFile('profile')]);
setView({type:'memories'});await settle();
assert.strictEqual(($('#stats').innerHTML.match(/class="stat"/g)||[]).length,1);
const level=$('#memory-level');level.value='profile';await level.onchange({target:level});
assert.strictEqual(($('#stats').innerHTML.match(/class="stat"/g)||[]).length,2);
assert($('#stats').innerHTML.includes('<b>1</b>'));assert($('#stats').innerHTML.includes('<b>2</b>'));
''')

    def test_compact_rows_collapse_details_and_escape_optional_metadata(self):
        self.run_js(r'''
const f=memoryFile('unknown'); f.path='/Users/test/.codex/memories/deep/note.md'; f.name='note.md';
f.semantics.appliesTo=[]; f.semantics.pipeline={stage:'background'};
f.memoryInfo={title:'<img src=x onerror=bad()> A title',relatedProjects:[
  {name:'<Project>',path:'/repos/<unsafe>',basis:'content <reference>',evidence:{path:'/proof/<note>.md',line:7}},null]};
api=async()=>memoryResponse([f]); setView({type:'memories'}); await settle();
const html=$('#content').innerHTML;
assert(html.includes('&lt;img src=x onerror=bad()&gt; A title'),'title must be useful plain text');
assert(!html.includes('<img')); assert(!html.includes('<Project>'));
assert(html.includes('内容关联≠适用范围'));
assert(html.includes('&lt;Project&gt;')); assert(html.includes('/repos/&lt;unsafe&gt;'));
assert(html.includes('content &lt;reference&gt;')); assert(html.includes('/proof/&lt;note&gt;.md')); assert(html.includes('L7'));
assert(!html.includes('不限项目'),'content mentions must not confer scope');
assert(html.includes('~/.codex/memories/deep/note.md'));
const entry=html.match(/<article class="memory-entry">([\s\S]*?)<\/article>/)[1];
const details=entry.match(/<details\b([^>]*)>([\s\S]*)<\/details>/);
assert(details,'reader/trigger/source metadata should be in a native disclosure');
assert(!/\bopen\b/.test(details[1]),'per-file details must start collapsed');
const visible=entry.slice(0,entry.indexOf('<details'));
assert(visible.includes('后台')); assert(visible.includes('自动加载')); assert(visible.includes('hermes')); assert(visible.includes('work'));
for(const text of ['读取方','Hermes Agent','触发条件','会话开始时','https://docs.example.test/memory','存放位置','/Users/test/']) {
  assert(details[2].includes(text),'detail retained: '+text);
  if(text!=='/Users/test/') assert(!visible.includes(text),'long detail not repeated in compact row: '+text);
}
assert(!visible.includes('未验证实际读取'),'shared warning belongs above rows, not each row');
// Optional legacy metadata stays safe without inventing title, stage or associations.
const old=memoryFile('unknown'); delete old.semantics; old.memoryInfo={title:{bad:true},relatedProjects:'not an array'};
assert(memoryEntryHtml(old).includes('阶段未知'));
assert(!memoryEntryHtml(old).includes('[object Object]'));
''')

    def test_search_accepts_displayed_short_paths_without_reading_source_fields(self):
        self.run_js(r'''
const f=memoryFile('profile'); f.path='/Users/test/.hermes/memories/MEMORY.md';
for(const field of ['content','snippet','body']) Object.defineProperty(f,field,{get(){throw new Error('must not read '+field);}});
api=async()=>memoryResponse([f]); setView({type:'memories'}); await settle();
const input=$('#memory-search'); input.value='~/.hermes/memories'; await input.oninput({target:input});
assert($('#content').innerHTML.includes('MEMORY.md'),'displayed shortened paths must be searchable');
assert($('#memory-page-status').textContent.includes('匹配 1'));
''')

    def test_local_transitions_refresh_and_stale_responses_preserve_drafts(self):
        self.run_js(r'''
const file={...memoryFile('profile'),content:'original',sha256:'draft-base'};
api=async()=>file; await openFile(file.path);
$('#v-text').value='unsaved raw'; $('#v-text').setSelectionRange(2,5); $('#v-text').scrollTop=31;
const draft={value:'unsaved section',defaultValue:'original section'};
$('#v-sec').querySelectorAll=s=>s==='textarea'?[draft]:[];
const loaded=current, version=viewerVersion;
const pending=[]; api=url=>new Promise((resolve,reject)=>pending.push({url,resolve,reject}));
const many=memoryResponse(Array.from({length:103},(_,i)=>memoryFile('unknown',i)));
setView({type:'memories'}); pending[0].resolve(many); await settle();
await $('#memory-next').onclick(); await $('#memory-next').onclick();
assert($('#memory-page-status').textContent.includes('101–103'));
const refreshing=$('#memory-refresh').onclick();
const search=$('#memory-search'); search.value='unknown2'; await search.oninput({target:search});
const stage=$('#memory-stage'); stage.value='unknown'; await stage.onchange({target:stage});
pending[1].resolve(many); await refreshing;
assert($('#memory-page-status').textContent.includes('1–11 / 匹配 11'));
assert.strictEqual(search.value,'unknown2'); assert.strictEqual(stage.value,'unknown');
search.value=''; await search.oninput({target:search});
await $('#memory-next').onclick(); await $('#memory-next').onclick();
const shrink=$('#memory-refresh').onclick(); pending[2].resolve(memoryResponse([memoryFile('unknown',90)])); await shrink;
assert($('#memory-page-status').textContent.includes('1–1')); assert.strictEqual($('#memory-next').disabled,true);
const stale=$('#memory-refresh').onclick();
const platform=$('#memory-platform');platform.value='codex';const fresh=platform.onchange({target:platform});
pending[4].resolve(memoryResponse([memoryFile('unknown',88)])); await fresh;
const good=$('#content').innerHTML; pending[3].resolve(many); await stale;
assert.strictEqual($('#content').innerHTML,good);
const departed=$('#memory-refresh').onclick(); setView({type:'assets'});
const library=$('#content').innerHTML; pending[5].resolve(many); await departed;
assert.strictEqual($('#content').innerHTML,library);
for(const id of ['prev','next','refresh','reset']) assert.strictEqual($('#memory-'+id).onclick,null);
assert.strictEqual(search.oninput,null); assert.strictEqual(stage.onchange,null);
assert.strictEqual(current,loaded);assert.strictEqual(viewerVersion,version);assert.strictEqual(current.sha256,'draft-base');
assert.strictEqual($('#v-text').value,'unsaved raw'); assert.strictEqual($('#v-text').selectionStart,2);
assert.strictEqual($('#v-text').selectionEnd,5); assert.strictEqual($('#v-text').scrollTop,31);
assert.strictEqual(draft.value,'unsaved section');
assert(pending.every(p=>p.url.startsWith('/api/memories?')));
''')

    def test_unavailable_and_metadata_only_catalog_entries_cannot_preview(self):
        self.run_js(r'''
const unavailable=memoryFile('unknown',1); unavailable.memoryInfo={status:'unavailable',title:'Rotated file',reason:'missing <file>'};
const metadata=memoryFile('unknown',2); metadata.memoryInfo={status:'metadata_only'};
const partial=memoryFile('unknown',3); partial.memoryInfo={status:'partial',truncated:true,title:'Partial title'};
const calls=[]; api=async url=>{calls.push(url);return memoryResponse([unavailable,metadata,partial]);};
setView({type:'memories'}); await settle();
const html=$('#content').innerHTML;
assert(html.includes('源文件不可用'),'rotated/deleted entries stay visible but honestly unavailable');
assert(!html.includes('data-asset-open="'+unavailable.path+'"'));
assert(!html.includes('data-asset-open="'+metadata.path+'"'));
assert(html.includes('data-asset-open="'+partial.path+'"'));
assert(html.includes('元数据仅解析前缀')); assert(html.includes('missing &lt;file&gt;'));
assert($('#memory-page-status').textContent.includes('匹配 3'));
assert.strictEqual(await openFile(unavailable.path),false); assert.strictEqual(await openFile(metadata.path),false);
assert.strictEqual(calls.length,1,'unavailable and metadata-only files never issue preview requests');
assert(!assetRowHtml(unavailable).includes('data-asset-open')); assert(!assetRowHtml(metadata).includes('data-asset-open'));
''')

    def test_local_declaration_provenance_and_pipeline_role_are_plain_text(self):
        self.run_js(r'''
const f=memoryFile('unknown');
f.semantics.basis={kind:'local_declaration',detail:'本机 <说明>',sources:[],localSources:[
 {path:'/config/<unsafe>.md',line:12,sha256:'hash<value>',label:'配置 <声明>'},null]};
f.semantics.pipeline={stage:'background'};
f.semantics.role={id:'resource',label:'可供后台与前台显式调用 <角色>'};
api=async()=>memoryResponse([f]);setView({type:'memories'});await settle();
const html=$('#content').innerHTML;
for(const text of ['本机声明','/config/&lt;unsafe&gt;.md','L12','hash&lt;value&gt;','配置 &lt;声明&gt;','可供后台与前台显式调用 &lt;角色&gt;']) assert(html.includes(text),text);
assert(!html.includes('href=')); assert(!html.includes('data-asset-open="/config/'));
assert(!html.includes('<unsafe>'));assert(!html.includes('仅供后台'));
''')


if __name__ == "__main__":
    unittest.main()
