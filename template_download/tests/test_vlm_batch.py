import unittest
import tempfile
import threading
import base64
import io
from pathlib import Path
from PIL import Image

from vlm_batch import validate_result, build_inputs, PROMPT, CodexServer


class ValidationTests(unittest.TestCase):
    def test_original_resolution_payload_and_classification_without_bbox(self):
        from vlm_stages import CROP_PROMPT, validate_stage
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'private-name.png'
            Image.new('RGB',(1080,1710),'blue').save(path)
            inputs,_=build_inputs([{'id':'SECRET','path':str(path)}],None,CROP_PROMPT)
            attachment=inputs[2]
            self.assertEqual(attachment['detail'],'original')
            with Image.open(io.BytesIO(base64.b64decode(attachment['url'].split(',',1)[1]))) as sent:
                self.assertEqual(sent.size,(1080,1710))
                self.assertEqual(sent.format,'PNG')
        validate_stage({'items':[{'id':'001','label':'single','needs_crop':True}]},[{'id':'001'}],'triage')
        validate_stage({'items':[{'id':'001','label':'collection','needs_crop':True}]},[{'id':'001'}],'triage')
        with self.assertRaises(ValueError):
            validate_stage({'items':[{'id':'001','label':'single','needs_crop':True,'bbox':[0,0,1000,1000]}]},[{'id':'001'}],'triage')

    def test_standard_tier_is_explicit_at_thread_and_turn(self):
        server = object.__new__(CodexServer)
        server.cwd = '.'
        server.disabled_mcp = {}
        server.events = {}
        server.lock = threading.RLock()
        calls = {}
        def rpc(method, params, **kwargs):
            calls[method] = params
            if method == 'thread/start':
                return {'thread': {'id': 'test'}}
            if method == 'turn/start':
                server.events['test'].put({'method':'item/completed','params':{
                    'item':{'type':'agentMessage','text':'{"items":[]}'}}})
                server.events['test'].put({'method':'turn/completed','params':{
                    'turn':{'status':'completed'}}})
                return {'turn':{'id':'turn'}}
            return {}
        server.rpc = rpc
        result = server.classify([], 'gpt-6-luna', 'low')
        self.assertEqual(calls['thread/start']['serviceTier'], 'default')
        self.assertEqual(calls['turn/start']['serviceTierForTurn'], 'default')
        self.assertEqual(calls['turn/start']['effort'], 'low')
        self.assertEqual(result['speed'], 'standard')

    def test_model_receives_no_source_ids_paths_or_labels(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'SECRET_collection.jpg'
            Image.new('RGB', (20, 20), 'white').save(path)
            inputs, wire = build_inputs([{'id': 'SECRET_diagram', 'path': str(path),
                                         'expected': 'SECRET_expected', 'origin': 'SECRET_origin'}])
            self.assertEqual(wire, [{'id': '001'}])
            self.assertEqual(inputs[0], {'type': 'text', 'text': PROMPT})
            self.assertEqual(inputs[1], {'type': 'text', 'text': 'image id: 001'})
            self.assertEqual(inputs[2]['type'], 'image')
            self.assertTrue(inputs[2]['url'].startswith('data:image/jpeg;base64,'))
            self.assertNotIn('SECRET', ''.join(i['text'] for i in inputs if i['type']=='text'))

    def test_exact_id_mapping(self):
        rows = [{'id': 'a'}, {'id': 'b'}]
        good = {'items': [{'id': 'a', 'label': 'single','action':'keep','bbox':None,'reason':'clean'},
                          {'id': 'b', 'label': 'collection','action':'reject','bbox':None,'reason':'multiple_templates'}]}
        self.assertEqual(validate_result(good, rows), good['items'])

    def test_missing_duplicate_reordered_unknown_labels_rejected(self):
        rows = [{'id': 'a'}, {'id': 'b'}]
        invalid = [
            [('a', 'single')],
            [('a', 'single'), ('a', 'single')],
            [('b', 'single'), ('a', 'single')],
            [('a', 'single'), ('b', 'accept')],
        ]
        for pairs in invalid:
            with self.subTest(pairs=pairs), self.assertRaises(ValueError):
                validate_result({'items': [{'id': i, 'label': v} for i, v in pairs]}, rows)


if __name__ == '__main__':
    unittest.main()
