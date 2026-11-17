"""V48 ordering, output isolation, bounded traces and exact-instance contracts."""
from contextlib import closing
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from RealDataAnalyse.conversation import Conversation
from RealDataAnalyse.excel import generate_example, import_files, DEFAULT_PROFILE
from RealDataAnalyse.plans import Plans, PlanRunner, capture, materialize
from RealDataAnalyse.runner import Runner
from RealDataAnalyse.storage import Ledger, sha256
from RealDataAnalyse.transport import Transport
from test_v44 import FakeService


class Contracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.stop = threading.Event()
        self.files = [Path(p) for p in generate_example(self.root, count=3, die_count=2)]
        (self.root/'source.gds').write_bytes(b'synthetic input')
        (self.root/'case.json').write_text(json.dumps({'gds_path':'source.gds','schema':5}))
        self.config = dict(folder=str(self.root),project=str(self.root/'case.json'),
            files=[dict(path=str(p),enabled=True) for p in self.files], profile=copy.deepcopy(DEFAULT_PROFILE),
            controls=dict(dies=2,workers=2,memory=512,retries=1,timeout=0), shared_mapping=True)
        self.plans = Plans(self.root/'plans.sqlite')
    def tearDown(self):self.temp.cleanup()
    def runner(self, database='run.sqlite', name='final.oas'):
        db=self.root/database
        if not db.exists():import_files(self.files,db,2,2,self.stop)
        return Runner(db,self.root/'ipc.json',self.root/'case.json',self.root/'Output'/name,transport=FakeService(self.root))
    def test_output_only_final_and_legacy_files_are_preserved_in_cache(self):
        output=self.root/'Output'; output.mkdir()
        (output/'final.oas').write_bytes(b'old final')
        (output/'partial.oas').write_bytes(b'old partial')
        (output/'old.lock').write_bytes(b'0')
        (output/'old-cache').mkdir(); (output/'old-cache/data').write_bytes(b'data')
        runner=self.runner(); runner.run()
        self.assertEqual([p.name for p in output.iterdir()],['final.oas'])
        with closing(Ledger(runner.database)) as ledger:
            record=ledger.get('publish')
            self.assertTrue(Path(record['stage']).is_relative_to(self.root/'Cache'))
            self.assertEqual(Path(record['archived_outputs'][str(runner.output)]['path']).read_bytes(),b'old final')
        self.assertEqual(next((self.root/'Cache').rglob('partial.oas')).read_bytes(),b'old partial')
        self.assertTrue(next((self.root/'Cache').rglob('old-cache/data')).is_file())
    def test_archive_and_publish_crash_recovers_without_recompute(self):
        runner=self.runner(); runner.output.parent.mkdir(); runner.output.write_bytes(b'previous')
        import RealDataAnalyse.runner as module
        replace=module.os.replace
        def interrupted(source,target):
            replace(source,target)
            if Path(target)==runner.output:raise OSError('power interruption after final rename')
        with patch.object(module.os,'replace',interrupted):
            with self.assertRaises(OSError):runner.run()
        count=runner.transport.begin_count
        runner.run()
        self.assertEqual(runner.transport.begin_count,count)
        self.assertEqual([p.name for p in runner.output.parent.iterdir()],['final.oas'])
    def test_reject_input_in_output_without_moving_any_input(self):
        runner=self.runner(); runner.output.parent.mkdir()
        source=runner.output.parent/'source.gds'; source.write_bytes(b'protected')
        (self.root/'case.json').write_text(json.dumps({'gds_path':str(source)}))
        with self.assertRaisesRegex(ValueError,'输入或任务'):runner.run()
        self.assertEqual(source.read_bytes(),b'protected');self.assertEqual(runner.transport.begin_count,0)
    def test_failed_geometry_preserves_old_output_and_extras(self):
        runner=self.runner();runner.transport.fail='geometry';runner.output.parent.mkdir();runner.output.write_bytes(b'old')
        extra=runner.output.parent/'diagnostic.txt';extra.write_bytes(b'note')
        with self.assertRaises(Exception):runner.run()
        self.assertEqual(runner.output.read_bytes(),b'old');self.assertEqual(extra.read_bytes(),b'note')
    def test_no_timeout_wait_is_cancellable_and_finite_timeout_still_applies(self):
        t=Transport(self.root/'endpoint')
        t.request=lambda *a,**k:dict(result=dict(status='running',progress=0))
        with self.assertRaises(TimeoutError):t.wait_task('id',self.stop,.01)
        event=threading.Event();timer=threading.Timer(.08,event.set);timer.start()
        try:
            with self.assertRaises(InterruptedError):t.wait_task('id',event,0)
        finally:timer.join()
    def test_conversation_is_bounded_redacted_and_complete_on_disk(self):
        trace=Conversation(capacity=3,message_limit=70);trace.set_root(self.root)
        for i in range(20):trace.record('send','endpoint',dict(id=i,authkey='never-log-me',data='x'*200))
        rows=trace.drain();trace.close()
        self.assertEqual(len(rows),4);self.assertIn('17',rows[0]);self.assertTrue(all(len(s)<150 for s in rows))
        full=(self.root/'logs/IPC-conversation.log').read_text()
        self.assertNotIn('never-log-me',full);self.assertEqual(full.count('[REDACTED]'),20);self.assertIn('x'*200,full)
    def test_instance_change_rejected_before_sending(self):
        endpoint=self.root/'ipc.json';endpoint.write_text(json.dumps({'instance_id':'new'}))
        t=Transport(endpoint,expected_instance='old')
        with patch('RealDataAnalyse.transport.call') as call:
            with self.assertRaisesRegex(RuntimeError,'实例已更换'):t.rpc('batch.submit')
            call.assert_not_called()
    def test_snapshots_are_independent_warn_conflicts_and_mark_missing_input(self):
        first=self.plans.add(capture(self.config,self.stop))
        changed=copy.deepcopy(self.config);changed['controls']['workers']=7
        second=self.plans.add(capture(changed,self.stop))
        self.assertEqual(self.plans.get(first)['snapshot']['controls']['workers'],2)
        self.assertEqual(self.plans.get(second)['snapshot']['controls']['workers'],7)
        self.assertEqual(self.plans.get(first)['severity'],'warning')
        self.assertEqual(self.plans.get(second)['status'],'pending')
        self.files[0].unlink();self.plans.recheck()
        self.assertEqual(self.plans.get(first)['severity'],'error')
    def test_snapshot_materialization_uses_project_cache(self):
        ident=self.plans.add(capture(self.config,self.stop));row=self.plans.get(ident)
        project=materialize(row,self.stop)
        self.assertTrue(project.is_relative_to(self.root/'Cache'))
        self.assertTrue((project.parent/json.loads(project.read_text())['gds_path']).is_file())
        self.assertFalse((self.root/'Tasks/Inputs').exists())
    def test_ordered_queue_skips_invalid_continues_after_error_and_never_repeats_completed(self):
        ids=[self.plans.add(capture(self.config,self.stop)) for _ in range(4)]
        self.plans.update(ids[1],status='error',severity='error')
        runner=PlanRunner(self.plans); seen=[]
        def run(row):
            seen.append(row['id'])
            if row['id']==ids[2]:raise ValueError('bad Excel data')
        with patch.object(runner,'run_one',run),patch.object(runner,'reconcile_archives'):
            result=runner.run();runner.run()
        self.assertEqual(seen,[ids[0],ids[2],ids[3]])
        self.assertEqual(result['completed'],2);self.assertEqual(result['failed'],1)
        completed=self.plans.get(ids[0]);self.assertEqual(completed['progress'],100);self.assertIsNotNone(completed['ended'])
    def test_input_change_after_capture_is_red_at_execution(self):
        from RealDataAnalyse.plans import validate
        snapshot=capture(self.config,self.stop);self.files[0].write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'已改变'):validate(snapshot,self.root/'Output/a.oas',True,self.stop)

if __name__=='__main__':unittest.main()
