"""Directory browsing uses the backend topology; no file-body or cloud requests."""
import json
import shutil
import subprocess
import unittest

from atlas.memory_storage import build_memory_storage
import test_frontend as frontend
import test_frontend_memory as memory

BASE = '/home/test/.codex/memories'
PATHS = [BASE+'/MEMORY.md', BASE+'/extensions/a/instructions.md', BASE+'/extensions/a/notes/one.md',
         BASE+'/extensions/ab/notes/two.md', '/home/test/.hermes/memories/USER.md']
TREE = build_memory_storage([{'path': p, 'category': 'memory'} for p in PATHS], home='/home/test')
FIXTURE = memory.FIXTURE + '\nconst storageTree=' + json.dumps(TREE) + ';\n' + r'''
const storageFiles=Object.keys(storageTree.locations).map((path,i)=>({...memoryFile('unknown',i),path,
  name:path.slice(path.lastIndexOf('/')+1),memoryStorage:storageTree.locations[path]}));
const storageResponse=Object.assign(memoryResponse(storageFiles),{storageTree});
const storageRows=()=>[...$('#content').innerHTML.matchAll(/data-asset-open="([^"]+)"/g)].map(m=>m[1]);
'''


@unittest.skipUnless(shutil.which('node'), 'Node is required for extracted JS checks')
class MemoryStorageFrontendTests(unittest.TestCase):
    def run_js(self, test):
        result = subprocess.run(['node','-e',frontend.HARNESS],
            input=json.dumps({'html':frontend.HTML,'script':frontend.SCRIPT,'test':FIXTURE+test}),
            capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_directory_mode_drills_into_real_folders_without_refetch_or_scope_inference(self):
        self.run_js(r'''
const calls=[]; api=async url=>{calls.push(url);return storageResponse;};
setView({type:'memories'});await settle();
assert.strictEqual(typeof $('#memory-by-directory')?.onclick,'function','directory view toggle missing');
await $('#memory-by-directory').onclick();
assert.strictEqual(view.mode,'directory');
assert($('#sidebar').innerHTML.includes('记忆存放目录'));
assert($('#content').innerHTML.includes('~/.codex/memories'));
assert.deepStrictEqual(storageRows(),[],'overview lists roots, not a duplicate flat list');
const base='/home/test/.codex/memories';
navigateMemoryDirectory(base);
assert.deepStrictEqual(storageRows(),[base+'/MEMORY.md']);
assert($('#content').innerHTML.includes('data-memory-dir="'+base+'/extensions"'));
assert($('#stats').innerHTML.includes('本层记忆'));assert($('#stats').innerHTML.includes('含子目录'));
const recursive=$('#memory-recursive'); recursive.checked=true; await recursive.onchange({target:recursive});
assert.strictEqual(storageRows().length,4);
navigateMemoryDirectory(base+'/extensions/a');
assert.strictEqual(storageRows().length,2);
assert(!storageRows().some(p=>p.includes('/ab/')),'prefix sibling must never become a descendant');
assert($('#crumb-inner').innerHTML.includes('data-memory-dir="'+base+'/extensions"'));
goBack();assert.strictEqual(view.directory,base);
await $('#memory-by-scope').onclick();assert.notStrictEqual(view.mode,'directory');
assert($('#content').innerHTML.includes('data-memory-level="unknown"'));
assert.strictEqual(calls.length,1,'folder navigation and mode switching are entirely local');
assert(storageFiles.every(f=>f.semantics.level==='unknown'));
''')


    def test_directory_paging_search_and_readonly_counts_cover_every_file(self):
        base = BASE + '/resources'
        paths = [base+'/%03d.md' % i for i in range(123)]
        tree = build_memory_storage([{'path': p, 'category': 'memory'} for p in paths], home='/home/test')
        self.run_js('const largeTree='+json.dumps(tree)+';\n'+r'''
const files=Object.keys(largeTree.locations).map((path,i)=>({...memoryFile('unknown',i),path,name:path,
  memoryStorage:largeTree.locations[path],memoryInfo:i===0?{status:'metadata_only'}:{status:'ready'}}));
const calls=[];api=async url=>{calls.push(url);return Object.assign(memoryResponse(files),{storageTree:largeTree});};
setView({type:'memories',mode:'directory'});await settle();navigateMemoryDirectory(largeTree.roots[0]);
const paths=()=>[...$('#content').innerHTML.matchAll(/class="memory-path" title="([^"]+)"/g)].map(m=>m[1]);
const seen=[];
while(true){const batch=paths();assert(batch.length<=50);seen.push(...batch);if($('#memory-next').disabled)break;await $('#memory-next').onclick();}
assert.strictEqual(seen.length,123);assert.strictEqual(new Set(seen).size,123);
assert.deepStrictEqual(seen.slice().sort(),files.map(f=>f.path).sort());
const search=$('#memory-search');search.value='000.md';await search.oninput({target:search});
assert.strictEqual(paths().length,1);assert.strictEqual(storageRows().length,0);
assert($('#memory-page-status').textContent.includes('匹配 1'));
assert($('#sidebar').innerHTML.includes('>1</span>'));assert($('#content').innerHTML.includes('仅元数据'));
assert.strictEqual(await openFile(files[0].path),false);assert.strictEqual(calls.length,1);
search.value='no-such-title';await search.oninput({target:search});
assert.strictEqual(paths().length,0);assert($('#content').innerHTML.includes('没有符合当前筛选'));
assert.strictEqual(view.directory,largeTree.roots[0]);
''')

    def test_directory_names_are_escaped_and_disappearing_selection_is_not_replaced(self):
        path = '/home/test/folder<unsafe>&\"/sub/MEMORY.md'
        tree = build_memory_storage([{'path': path, 'category': 'memory'}], home='/home/test')
        self.run_js('const unsafeTree='+json.dumps(tree)+';\n'+r'''
const path=Object.keys(unsafeTree.locations)[0];const file={...memoryFile('unknown'),path,memoryStorage:unsafeTree.locations[path]};
api=async()=>Object.assign(memoryResponse([file]),{storageTree:unsafeTree});
DATA=null;setView({type:'memories',mode:'directory'});await settle();
assert($('#sidebar').innerHTML.includes('&lt;unsafe&gt;&amp;&quot;'));
assert(!$('#content').innerHTML.includes('<unsafe>'));
navigateMemoryDirectory(unsafeTree.roots[0]);
const selected=view.directory;
api=async()=>storageResponse;await $('#memory-refresh').onclick();
assert.strictEqual(view.directory,selected);assert.strictEqual(storageRows().length,0);
assert($('#content').innerHTML.includes('不在当前记忆清单'));
assert(!$('#crumb-inner').innerHTML.includes('<unsafe>'));
assert.strictEqual(navigateMemoryDirectory('/not/in/catalog'),false);
const pending=[];api=url=>new Promise(resolve=>pending.push(resolve));
const stale=$('#memory-refresh').onclick();setView({type:'assets'});
const content=$('#content').innerHTML;pending[0](storageResponse);await stale;
assert.strictEqual($('#content').innerHTML,content);assert.strictEqual(MEMORIES,null);
''')

    def test_viewer_locates_siblings_without_losing_draft_and_resets_scope_filters(self):
        self.run_js(r'''
const file={...storageFiles.find(f=>f.path.endsWith('/one.md')),content:'original',sha256:'base'};
api=async()=>file; await openFile(file.path);
$('#v-text').value='unsaved draft'; $('#v-text').setSelectionRange(2,6); $('#v-text').scrollTop=27;
const selected=current, version=viewerVersion;
const draft={value:'section draft',defaultValue:'old'}; $('#v-sec').querySelectorAll=s=>s==='textarea'?[draft]:[];
memoryFilters.platform='other'; memoryLocal.query='no matches'; memoryLocal.stage='foreground'; memoryLocal.level='user';
const calls=[]; api=async url=>{calls.push(url);return storageResponse;};
const button=$('#v-directory'); assert(button,'viewer needs a location entry');
assert.strictEqual(button.hidden,false); assert.strictEqual(button.disabled,false);
await button.listeners.click();await settle();
assert.strictEqual(view.mode,'directory'); assert.strictEqual(view.directory,file.memoryStorage.directory);
assert(storageRows().includes(file.path)); assert.strictEqual(memoryRecursive,false);
assert(Object.values(memoryFilters).every(v=>!v));assert(Object.values(memoryLocal).every(v=>!v));
assert.strictEqual(current,selected);assert.strictEqual(viewerVersion,version);
assert.strictEqual($('#v-text').value,'unsaved draft');assert.strictEqual($('#v-text').selectionStart,2);
assert.strictEqual($('#v-text').selectionEnd,6);assert.strictEqual($('#v-text').scrollTop,27);assert.strictEqual(draft.value,'section draft');
assert.strictEqual(calls.length,1);assert(calls[0].startsWith('/api/memories?'));
setView({type:'assets'});
for (const id of ['memory-by-directory','memory-by-scope']) assert.strictEqual($('#'+id).onclick,null);
assert.strictEqual($('#memory-recursive').onchange,null);
current={path:'/work/AGENTS.md',category:'instruction',editable:true};updateViewerAccess();
assert.strictEqual(button.hidden,true);const before=calls.length;await button.listeners.click();assert.strictEqual(calls.length,before);
''')


if __name__ == '__main__':
    unittest.main()
