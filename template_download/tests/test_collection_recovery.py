import json
import tempfile
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from browser import AccessStopped
from store import Store
from stage_cli import collect_worker
from pipeline import state_get, state_set
from mediacrawler_adapter import import_export


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name)
        self.args=SimpleNamespace(worker_retries=2,worker_timeout=360,delay=15)
        self.cursor={'keyword':0,'page':3,'offset':5}
        state_set(self.store,'cursor',self.cursor)

    def tearDown(self):
        self.store.db.close();self.tmp.cleanup()

    def process(self,result):
        def launch(command,**kwargs):
            out=Path(command[-1]);(out/'collection-report.json').write_text(json.dumps(result))
            return SimpleNamespace(returncode=0 if result['status']=='success' else 2,wait=lambda **kwargs:None)
        return launch

    def test_461_not_retried_and_cursor_unchanged(self):
        result={'status':'error','http_responses':[{'http_status':461}]}
        with patch('stage_cli.subprocess.Popen',side_effect=self.process(result)) as launch:
            with self.assertRaises(AccessStopped):collect_worker(self.store,{'keywords':['fixture']},self.args,self.cursor)
        self.assertEqual(launch.call_count,1)
        self.assertEqual(state_get(self.store,'cursor',None),self.cursor)
        self.assertTrue(state_get(self.store,'collection_requires_attention',False))

    def test_crash_retry_is_bounded(self):
        with patch('stage_cli.subprocess.Popen',side_effect=self.process({'status':'error'})) as launch,patch('stage_cli.time.sleep'):
            with self.assertRaises(RuntimeError):collect_worker(self.store,{'keywords':['fixture']},self.args,self.cursor)
        self.assertEqual(launch.call_count,3)
        self.assertEqual(state_get(self.store,'cursor',None),self.cursor)

    def test_recover_only_unterminated_tail(self):
        path=self.store.root/'notes.jsonl'
        valid=json.dumps({'note_id':'a'*24,'image_list':['https://example.com/image.jpg']})+'\n'
        path.write_text(valid+'{"note_id":')
        import_export(self.store,path,recover_partial=True)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0],1)
        path.write_text(valid+'{broken}\n')
        with self.assertRaises(json.JSONDecodeError):import_export(self.store,path,recover_partial=True)

    def test_hung_worker_terminated_without_advancing_cursor(self):
        from unittest.mock import Mock
        process=Mock(pid=12345,returncode=1)
        process.wait.side_effect=[subprocess.TimeoutExpired('fixture',360),1]
        self.args.worker_retries=0
        with patch('stage_cli.subprocess.Popen',return_value=process),patch('stage_cli.subprocess.run') as kill:
            with self.assertRaises(RuntimeError):collect_worker(self.store,{'keywords':['fixture']},self.args,self.cursor)
        import os
        if os.name=='nt':self.assertEqual(kill.call_args.args[0],['taskkill','/PID','12345','/T','/F'])
        else:process.kill.assert_called_once()
        self.assertEqual(state_get(self.store,'cursor',None),self.cursor)


if __name__=='__main__':unittest.main()
