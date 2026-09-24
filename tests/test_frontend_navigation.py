"""Navigation restores browsing state without replaying cloud requests."""
import json
import shutil
import subprocess
import unittest

import test_frontend as frontend
import test_frontend_memory_storage as storage


@unittest.skipUnless(shutil.which('node'), 'Node is required')
class NavigationTests(unittest.TestCase):
    def run_js(self, source):
        result = subprocess.run(['node', '-e', frontend.HARNESS],
                                input=json.dumps({'html': frontend.HTML, 'script': frontend.SCRIPT,
                                                  'test': storage.FIXTURE + source + '\nconsole.log("NAV_COMPLETE");'}),
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('NAV_COMPLETE', result.stdout)

    def test_back_restores_cloud_search_filters_and_results_without_network(self):
        self.run_js(r'''
const calls=[];api=async url=>{calls.push(url);throw new Error('navigation must not query');};
view={type:'assets'}; hist=[];
assetFilters.platform='hermes';assetFilters.category='memory';assetPage=2;
Object.assign(searchState,{q:'exact query',mode:'vector',results:[{path:'/result.md',name:'result.md',snippet:'cached result'}],total:1});
$('#search').value='exact query';$('#search-mode').value='vector';
current={path:'/draft.md',content:'saved',editable:true};$('#v-text').value='unsaved draft';
setView({type:'all'});
assetFilters.platform='codex';assetFilters.category='';assetPage=0;
goBack();
assert.strictEqual(view.type,'assets');
assert.strictEqual(searchState.q,'exact query');assert.strictEqual($('#search').value,'exact query');
assert.strictEqual(searchState.mode,'vector');assert.strictEqual($('#search-mode').value,'vector');
assert.strictEqual(searchState.results[0].snippet,'cached result');
assert.strictEqual(assetFilters.platform,'hermes');assert.strictEqual(assetFilters.category,'memory');
assert.strictEqual($('#v-text').value,'unsaved draft');assert.strictEqual(calls.length,0);
assert($('#content').innerHTML.includes('cached result'));
''')

    def test_location_and_back_restore_memory_filters_page_and_drafts(self):
        self.run_js(r'''
const files=Array.from({length:130},(_,i)=>({...memoryFile('user',i),platform:'hermes'}));
const response=memoryResponse(files);
const calls=[];api=async url=>{calls.push(url);return response;};
setView({type:'memories',mode:'scope'});await settle();hist=[];
memoryFilters.platform='hermes';memoryLocal.query='user';memoryLocal.level='user';
memoryPage=2;memoryRecursive=true;memoryExpanded.add('/keep-expanded');renderMemoryScope();
current={path:'/source.md',category:'memory',memoryStorage:{directory:'/storage',ancestors:[]}};
$('#v-text').value='draft';$('#v-text').setSelectionRange(1,3);$('#v-text').scrollTop=17;
$('#v-directory').listeners.click();await settle();
assert.strictEqual(memoryFilters.platform,'');assert.strictEqual(memoryLocal.query,'');
const before=calls.length;goBack();await settle();
assert.strictEqual(view.mode,'scope');assert.strictEqual(memoryFilters.platform,'hermes');
assert(!$('#notice').textContent.includes('已清除记忆筛选'));
assert.strictEqual(memoryLocal.query,'user');assert.strictEqual(memoryLocal.level,'user');
assert.strictEqual(memoryPage,2);assert.strictEqual(memoryRecursive,true);
assert(memoryExpanded.has('/keep-expanded'));assert.strictEqual(calls.length,before,'restore a current cache');
assert($('#memory-page-status').textContent.includes('101–130'));
assert.strictEqual($('#v-text').value,'draft');assert.strictEqual($('#v-text').selectionStart,1);
assert.strictEqual($('#v-text').scrollTop,17);
''')

    def test_locate_reloads_when_it_clears_server_filters(self):
        self.run_js(r'''
const calls=[];
api=async url=>{calls.push(url);return new URL(url,'http://localhost').searchParams.get('platform')?
 {...memoryResponse(storageFiles.slice(0,1)),storageTree}:storageResponse;};
memoryFilters.platform='codex';
setView({type:'memories',mode:'scope'});await settle();
assert.strictEqual(MEMORIES.files.length,1);
current={category:'memory',memoryStorage:{directory:'/home/test/.codex/memories',ancestors:[]}};
$('#v-directory').listeners.click();await settle();
assert.strictEqual(memoryFilters.platform,'');
assert.strictEqual(MEMORIES.files.length,storageFiles.length,'unfiltered directory cannot reuse a filtered response');
assert.strictEqual(calls.length,2);
assert.strictEqual(new URL(calls[1],'http://localhost').searchParams.get('platform'),'');
''')

    def test_back_does_not_restore_stale_or_unfinished_cloud_results(self):
        self.run_js(r'''
const calls=[];api=async url=>{calls.push(url);throw new Error('no automatic cloud query');};
Object.assign(searchState,{q:'query',mode:'hybrid',results:[{path:'/removed.md',snippet:'outdated'}],total:1});
setView({type:'all'});
ASSETS={files:[],totalFiles:0};
goBack();
assert.strictEqual(searchState.q,'query');assert.strictEqual(searchState.results,null);
assert(searchState.warning.includes('已更新') && searchState.warning.includes('手动'));
assert(!$('#content').innerHTML.includes('outdated'));assert.strictEqual(calls.length,0);
searchState.loading=true;
setView({type:'all'});goBack();
assert.strictEqual(searchState.loading,false);
assert(searchState.warning.includes('未完成'));assert.strictEqual(calls.length,0);
''')

    def test_same_directory_locate_keeps_a_return_state_and_history_is_bounded(self):
        self.run_js(r'''
api=async()=>storageResponse;
setView({type:'memories',mode:'directory',directory:'/home/test/.codex/memories'});await settle();
hist=[];memoryFilters.platform='hermes';memoryLocal.query='some filter';
current={category:'memory',memoryStorage:{directory:view.directory,ancestors:[]}};
$('#v-directory').listeners.click();await settle();
assert.strictEqual(hist.length,1);goBack();
assert.strictEqual(memoryFilters.platform,'hermes');assert.strictEqual(memoryLocal.query,'some filter');
for(let i=0;i<80;i++) setView({type:'assets',fixture:i});
assert.strictEqual(hist.length,60);
''')


if __name__ == '__main__':
    unittest.main()
