"""Instruction-map counts share the rendered worktree filter."""
import json
import shutil
import subprocess
import unittest
import test_frontend as frontend

FIXTURE = r'''
DATA={files:[
 {path:'/repo/AGENTS.md',name:'AGENTS.md',kind:'agents',root:'host',proj:'repo',sub:'',scope:'project',bytes:10,mtime:1,worktree:false,dup:false},
 {path:'/worktrees/repo/AGENTS.md',name:'AGENTS.md',kind:'agents',root:'host',proj:'repo',sub:'copy',scope:'project',bytes:20,mtime:2,worktree:true,dup:false}],
 roots:[{name:'host',files:2,bytes:30,mtime:2,projects:[{name:'repo',files:2,bytes:30,mtime:2,dirs:[]}]}],
 totalFiles:2,uniqueFiles:2,dupFiles:0,dupGroups:[],totalBytes:30,durationMs:1};
ASSETS={files:[],totalFiles:0};
'''


@unittest.skipUnless(shutil.which('node'), 'Node is required')
class MapConsistencyTests(unittest.TestCase):
    def run_js(self, source):
        result = subprocess.run(['node','-e',frontend.HARNESS],
            input=json.dumps({'html':frontend.HTML,'script':frontend.SCRIPT,
                              'test':FIXTURE+source+'\nconsole.log("MAP_COMPLETE");'}),
            capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('MAP_COMPLETE',result.stdout)

    def test_hidden_worktrees_have_consistent_counts_bytes_and_release_controls(self):
        self.run_js(r'''
hideWt=true;
for(const target of [{type:'all'},{type:'root',root:'host'},{type:'proj',root:'host',proj:'repo'}]) {
 setView(target);
 const stats=$('#stats').innerHTML, content=$('#content').innerHTML;
 assert(stats.includes('<b>1</b>当前显示文件 / 总计 2'),stats);
 assert(stats.includes('<b>10 B</b>显示字节 / 总计 30 B'),stats);
 assert(content.includes('id="btn-wt"'),'every filtered map view can unhide');
 assert(!content.includes('data-path="/worktrees/repo/AGENTS.md"'));
 assert($('#sidebar').innerHTML.includes('>1 / 2</span>'),'sidebar shows visible and total');
}
toggleWt();
assert($('#stats').innerHTML.includes('<b>2</b>文件'));
assert($('#stats').innerHTML.includes('<b>30 B</b>字节'));
assert($('#content').innerHTML.includes('data-path="/worktrees/repo/AGENTS.md"'));
''')

    def test_nested_summaries_and_empty_filtered_projects_explain_the_same_totals(self):
        self.run_js(r'''
hideWt=true;
for(const html of [allRootsHtml(),oneRootHtml('host'),projectHtml(DATA.roots[0],DATA.roots[0].projects[0])]) {
 assert(html.includes('显示 1 / 总计 2 文件'),html);
 assert(html.includes('显示 10 B / 总计 30 B'),html);
}
DATA.files[0].worktree=true;
setView({type:'proj',root:'host',proj:'repo'});
assert($('#stats').innerHTML.includes('<b>0</b>当前显示文件 / 总计 2'));
assert($('#content').innerHTML.includes('已隐藏，点击显示'));
assert($('#content').innerHTML.includes('当前没有可见文件'));
assert(!$('#content').innerHTML.includes('data-path='));
''')

    def test_user_scope_copy_is_a_location_candidate_not_a_loading_promise(self):
        self.run_js(r'''
DATA.files[0].scope='user';setView({type:'user'});
const html=$('#content').innerHTML;
assert(html.includes('位置候选') && html.includes('不代表'),html);
assert(!html.includes('对你所有项目生效'));assert(!html.includes('项目级文件会覆盖它们'));
assert($('#stats').innerHTML.includes('<b>1</b>文件'));
''')


if __name__=='__main__':
    unittest.main()
