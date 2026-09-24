"""Refresh coordinates map snapshots and the asset job without cloud replay."""
import json
import shutil
import subprocess
import unittest
import test_frontend as frontend

FIXTURE = r'''
const oldMap={files:[{path:'/removed/AGENTS.md',name:'AGENTS.md',root:'fixture',proj:'repo',bytes:1}],roots:[],totalFiles:1,dupFiles:0,generatedAt:10,host:'fixture'};
const freshMap={...oldMap,files:[],totalFiles:0,generatedAt:20};
DATA=oldMap;ASSETS={files:oldMap.files,totalFiles:1,generatedAt:10,instructionGeneratedAt:10};
loadUsage=async()=>true;
'''


@unittest.skipUnless(shutil.which('node'), 'Node is required')
class RefreshFrontendTests(unittest.TestCase):
    def run_js(self, source):
        result=subprocess.run(['node','-e',frontend.HARNESS],
            input=json.dumps({'html':frontend.HTML,'script':frontend.SCRIPT,
                              'test':FIXTURE+source+'\nconsole.log("REFRESH_COMPLETE");'}),
            text=True,capture_output=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('REFRESH_COMPLETE',result.stdout)

    def test_asset_rescan_reads_updated_map_and_catalog_with_drafts_intact(self):
        self.run_js(r'''
const calls=[];
api=async(url,opts)=>{calls.push({url,opts});
 if(url==='/api/assets/rescan')return {stdout:'scanned',job:{running:false,stage:'done'}};
 if(url==='/api/tree')return freshMap;
 if(url==='/api/assets')return {files:[],totalFiles:0,generatedAt:21,instructionGeneratedAt:20};
 if(url==='/api/assets/status')return {job:{running:false},index:{}};
 throw new Error(url);
};
current={path:'/draft.md',content:'saved',editable:true};$('#v-text').value='unsaved';
assetFilters.platform='hermes';
await startAssetJob('rescan');
assert.strictEqual(DATA.generatedAt,20);assert.strictEqual(DATA.files.length,0);
assert.strictEqual(ASSETS.generatedAt,21);assert.strictEqual(assetFilters.platform,'hermes');
assert.strictEqual($('#v-text').value,'unsaved');
assert.deepStrictEqual(calls.filter(c=>c.opts?.method==='POST').map(c=>[c.url,JSON.parse(c.opts.body)]),[['/api/assets/rescan',{}]]);
assert(calls.some(c=>c.url==='/api/tree'));
''')

    def test_fast_rescan_invalidates_old_cloud_results_without_resubmitting(self):
        self.run_js(r'''
Object.assign(searchState,{q:'cached query',mode:'vector',results:[{path:'/removed.md',snippet:'stale result'}],total:1});
const calls=[];
api=async(url,opts)=>{calls.push(url);
 if(url==='/api/assets/rescan')return {stdout:'scanned',job:{running:false,stage:'done'}};
 if(url==='/api/tree')return freshMap;
 if(url==='/api/assets')return {files:[],totalFiles:0,generatedAt:21,instructionGeneratedAt:20};
 if(url==='/api/assets/status')return {job:{running:false},index:{}};
 throw new Error(url);
};
await startAssetJob('rescan');
assert.strictEqual(searchState.q,'cached query');
assert.strictEqual(searchState.results,null);
assert(searchState.warning.includes('重新提交'));
assert(!calls.some(url=>url.startsWith('/api/search?')));
''')

    def test_job_completion_reads_the_catalog_only_once(self):
        self.run_js(r'''
for (const running of [true,false]) {
 const calls=[];
 api=async(url,opts)=>{calls.push(url);
  if(url==='/api/assets/rescan')return {stdout:'scanned',job:{running,stage:running?'keyword':'done'}};
  if(url==='/api/tree')return freshMap;
  if(url==='/api/assets')return {files:[],totalFiles:0,generatedAt:21,instructionGeneratedAt:20};
  if(url==='/api/assets/status')return {job:{running:false},index:{}};
  throw new Error(url);
 };
 assetStatus={job:{running:false},index:{}};
 await startAssetJob('rescan');
 assert.strictEqual(calls.filter(url=>url==='/api/assets').length,1,'one completion must not download the full catalog twice');
}
''')

    def test_transient_status_failure_retries_only_the_existing_job_read(self):
        self.run_js(r'''
const calls=[];let unavailable=true;
assetStatus={job:{running:true,stage:'keyword'},index:{}};
api=async(url,opts)=>{calls.push({url,opts});
 if(url==='/api/assets/status') {
  if(unavailable)throw new Error('temporary loopback failure');
  return {job:{running:false,stage:'done'},index:{}};
 }
 if(url==='/api/assets')return {files:[],totalFiles:0,generatedAt:20,instructionGeneratedAt:10};
 throw new Error(url);
};
await refreshAssetStatus();
assert($('#index-status').textContent.includes('temporary loopback failure'));
assert($('#index-status').textContent.includes('自动重试'));
assert.strictEqual($('#btn-rescan').disabled,true,'unknown completion must not enable a duplicate task');
assert.strictEqual(timers.size,1,'a temporary read failure must not leave the running job stuck forever');
const retry=[...timers.values()][0];unavailable=false;await retry();
assert.strictEqual(assetStatus.job.running,false);
assert.strictEqual(timers.size,0);
assert.strictEqual($('#btn-rescan').disabled,false);
assert(!calls.some(call=>call.opts?.method==='POST'));
assert.strictEqual(calls.filter(call=>call.url==='/api/assets').length,1);
''')

    def test_job_read_and_worker_errors_are_visible_outside_the_file_library(self):
        self.run_js(r'''
for (const type of ['memories','all']) {
 view={type};assetStatus={job:{running:true,stage:'keyword'}};
 api=async()=>{throw new Error('temporary status failure');};
 await refreshAssetStatus();
 const text=$('#snapshot-status').textContent;
 assert(text.includes('temporary status failure') && text.includes('自动重试'),text);
 assetStatusError='';assetStatus={job:{running:false,stage:'error',error:'local index failed'}};
 renderAssetStatus();
 assert($('#snapshot-status').textContent.includes('local index failed'));
}
''')

    def test_snapshot_status_discloses_dates_missing_entries_and_pending_work(self):
        self.run_js(r'''
ASSETS={files:[],totalFiles:0,generatedAt:21,instructionGeneratedAt:20};
renderContent();
assert($('#snapshot-status'),'shared snapshot status must be visible in all views');
let text=$('#snapshot-status').textContent;
assert(text.includes('指令地图') && text.includes(fmtT(10)),text);
assert(text.includes('文件库') && text.includes(fmtT(21)),text);
assert(text.includes('未对齐') && text.includes('重新扫描'),text);
assert(text.includes('1 条地图记录不在安全文件库'),text);
DATA=freshMap;renderContent();
assert(!$('#snapshot-status').textContent.includes('未对齐'));
assetStatus={job:{running:true,stage:'keyword'}};renderAssetStatus();
assert($('#snapshot-status').textContent.includes('更新中'));
assert.strictEqual($('#btn-rescan').disabled,true);
const calls=[];api=async url=>{calls.push(url);throw new Error('should not submit twice');};
await $('#btn-rescan').listeners.click({target:$('#btn-rescan')});
assert.strictEqual(calls.length,0);
''')

    def test_catalog_source_version_reloads_the_map_and_refreshes_status(self):
        self.run_js(r'''
const calls=[];api=async url=>{calls.push(url);return url==='/api/tree'?freshMap:
 {files:[],totalFiles:0,generatedAt:21,instructionGeneratedAt:20};};
await loadAssets();
assert.strictEqual(DATA.generatedAt,20);
assert(calls.includes('/api/tree'));
assert($('#snapshot-status').textContent.includes(fmtT(21)));
assert(!$('#snapshot-status').textContent.includes('未对齐'));
''')

    def test_map_read_failure_stays_visible_and_does_not_report_success(self):
        self.run_js(r'''
api=async(url,opts)=>{
 if(url==='/api/assets/rescan')return {job:{running:false},stdout:'scanned'};
 if(url==='/api/tree')throw new Error('map read failed');
 if(url==='/api/assets/status')return {job:{running:false},index:{}};
 return {files:[],totalFiles:0,generatedAt:21,instructionGeneratedAt:20};
};
await startAssetJob('rescan');
assert($('#snapshot-status').textContent.includes('地图读取失败'));
assert(!$('#notice').textContent.includes('已重新扫描'));
assert.strictEqual(DATA.generatedAt,10);
''')

    def test_map_loads_reject_out_of_order_responses(self):
        self.run_js(r'''
const pending=[];api=()=>new Promise(resolve=>pending.push(resolve));
const old=loadTree(), recent=loadTree();
pending[1](freshMap);await recent;
pending[0](oldMap);await old;
assert.strictEqual(DATA.generatedAt,20);
''')


if __name__=='__main__':
    unittest.main()
