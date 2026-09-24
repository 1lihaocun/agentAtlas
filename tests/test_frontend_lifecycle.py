"""Execute the real page and lifecycle renderer in the existing Node DOM harness."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

try:
    from .test_frontend import HARNESS, HTML, SCRIPT
except ImportError:
    from test_frontend import HARNESS, HTML, SCRIPT

RENDERER = (Path(__file__).resolve().parents[1] / "web/lifecycle.js").read_text()


@unittest.skipUnless(shutil.which("node"), "Node required")
class LifecycleFrontendTests(unittest.TestCase):
    def run_js(self, test):
        result = subprocess.run(["node", "-e", HARNESS], input=json.dumps({
            "html": HTML, "script": RENDERER + "\nconst AtlasLifecycle = window.AtlasLifecycle;\n" + SCRIPT,
            "test": test}), text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unknown_is_not_unused_and_sidebar_uses_review_count(self):
        self.run_js(r'''
DATA={files:[{path:'/a',kind:'agents'}],roots:[],dupFiles:0};
USAGE={days:30,files:{'/a':{path:'/a',estimatedSessions:null,confirmedReads:null,mentions:0,sourceStatus:'missing'}}};
assert(!fileRow(DATA.files[0]).includes('未用'));
assert(fileRow(DATA.files[0]).includes('未知'));
RETIREMENT={total:1,rows:[{path:'/a',bucket:'unknown',reason:'missing',sourceStatus:'missing'}],
  counts:{review:0,unknown:1,kept:0,snoozed:0,active:0,recent:0,excluded:0},warnings:[],days:30,staleDays:90};
view={type:'usage'}; retirementBucket='unknown';
renderContent(); renderSidebar();
assert($('#content').innerHTML.includes('不自动删除'));
assert($('#content').innerHTML.includes('/a'));
assert($('#sidebar').innerHTML.includes('淘汰审阅'));
''')

    def test_decision_posts_version_and_reads_queue_back_without_source_save(self):
        self.run_js(r'''
const calls=[];
const data={total:0,rows:[],counts:{review:0,unknown:0,kept:0,snoozed:0,active:0,recent:0,excluded:0}};
api=async (url,opts)=>{calls.push({url,opts});return data;};
view={type:'assets'};
await submitRetirementDecision('/a','abc','keep');
assert.strictEqual(calls[0].url,'/api/retirement/decision');
const body=JSON.parse(calls[0].opts.body);
assert.deepStrictEqual(body,{path:'/a',baseVersion:'abc',action:'keep'});
assert(calls.some(c=>c.url.startsWith('/api/retirement?')));
assert(!calls.some(c=>c.url==='/api/save'));
assert.strictEqual(RETIREMENT,data);
''')

    def test_failed_decision_keeps_queue_and_displays_error(self):
        self.run_js(r'''
const original={rows:[]}; RETIREMENT=original;
api=async()=>{throw new Error('文件内容已经变化');};
await submitRetirementDecision('/a','old','keep');
assert.strictEqual(RETIREMENT,original);
assert($('#notice').textContent.includes('文件内容已经变化'));
assert.strictEqual(retirementBusy,false);
''')

    def test_section_control_carries_identity_not_only_lines(self):
        self.run_js(r'''
current={editable:true,path:'/a',sha256:'hash',content:'## A\n'};
renderSections([{id:'opaque-versioned-id',level:2,title:'A',chars:5,start_line:1,end_line:1,text:'## A\n'}]);
assert($('#v-sec').innerHTML.includes('data-section-id="opaque-versioned-id"'));
''')

    def test_old_usage_response_cannot_overwrite_readback_after_decision(self):
        self.run_js(r'''
const pending={}; api=url=>new Promise(resolve=>pending[url]=resolve);
const old=loadUsage();
const fresh={total:0,rows:[],counts:{review:0,unknown:0,kept:0,snoozed:0,active:0,recent:0,excluded:0}};
api=async()=>fresh;
await submitRetirementDecision('/a','v1','keep');
pending['/api/usage?days=30']({files:[],days:30});
pending['/api/retirement?days=30&staleDays=90']({rows:[],marker:'stale'});
await old;
assert.strictEqual(RETIREMENT,fresh);
''')

    def test_effective_panel_uses_codex_metadata_and_marks_limits(self):
        self.run_js(r'''
current={path:'/repo/AGENTS.md',editable:true,sha256:'x',content:'rules',category:'instruction'};
const urls=[]; api=async url=>{urls.push(url);return {dir:'/repo',tool:'Codex',files:[
 {path:'/repo/AGENTS.md',bytes:12,totalBytes:12,indexed:true,scope:'project'}],warnings:[],sourceStatus:'available'};};
await $('#v-effective').listeners.click();
assert(urls[0].includes('tool=Codex'));
assert($('#eff-meta').textContent.includes('推算'));
assert($('#eff-body').innerHTML.includes('/repo/AGENTS.md'));
assert($('#eff-body').innerHTML.includes('data-effective-open'));
''')

    def test_evidence_popover_ignores_out_of_order_response(self):
        self.run_js(r'''
elements.set('#reads-pop',document.createElement('div'));
const requests=[];
api=url=>new Promise(resolve=>requests.push({url,resolve}));
const old=showUsageEvidence('/old',40,80), fresh=showUsageEvidence('/fresh',40,80);
assert(requests.every(r=>r.url.startsWith('/api/usage/evidence?')));
requests[1].resolve({path:'/fresh',evidence:[],sourceStatus:'available'});await fresh;
requests[0].resolve({path:'/old',evidence:[],sourceStatus:'available'});await old;
assert($('#reads-pop').innerHTML.includes('/fresh'));
assert(!$('#reads-pop').innerHTML.includes('/old'));
assert($('#reads-pop').innerHTML.includes('不能据此断言未使用'));
''')
