import asyncio
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from store import Store, extract_notes
from quality import inspect_image, near_match, bands
from download import download
from browser import AccessStopped, access_error
from run import review_page

BASE = Path(__file__).resolve().parents[1]
ID = '0123456789abcdef01234567'


def picture(size=(1000,1200), color='red'):
    buf = io.BytesIO()
    Image.new('RGB',size,color).save(buf,format='PNG')
    return buf.getvalue()


class WorkflowTests(unittest.TestCase):
    def test_security_redirect_stops_even_with_http_200(self):
        self.assertIn('300012',access_error('https://www.xiaohongshu.com/website-login/error','安全限制','IP存在风险\n300012'))
        self.assertIsNone(access_error('https://www.xiaohongshu.com/explore/'+ID,'拼贴模板','正常笔记内容'))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.cfg = json.loads((BASE/'config.json').read_text(encoding='utf-8'))
        self.cfg['download_delay_seconds'] = 0

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def seed(self, urls):
        self.store.ingest({'note_id':ID,'title':'拼贴','image_list':[{'url_default':x} for x in urls]})

    def execute(self, handler, limit=100):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch('download.httpx.AsyncClient',return_value=client):
            asyncio.run(download(self.store,self.cfg,limit))

    def test_parser_camel_and_search_cover(self):
        payload={'data':{'items':[{'note_card':{'noteId':ID,'imageList':[{'urlDefault':'https://example.com/a'}]}}]}}
        self.assertEqual(len(list(extract_notes(payload))),1)
        self.assertEqual(list(extract_notes({'id':ID,'cover':{'url':'https://example.com/a'}})),[])

    def test_ingest_idempotent_and_refresh_url(self):
        self.seed(['https://example.com/a'])
        self.seed(['https://example.com/b'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM images').fetchone()[0],1)
        self.assertEqual(self.store.db.execute('SELECT url FROM images').fetchone()[0],'https://example.com/b')

    def test_resolution_and_invalid(self):
        self.assertEqual(inspect_image(picture((100,100)),self.cfg)['reason'],'resolution')
        with self.assertRaises(Exception):
            inspect_image(b'<html>blocked</html>',self.cfg)

    def test_band_index_matches_four_bit_distance(self):
        self.seed(['https://example.com/a'])
        self.store.db.execute("UPDATE images SET dhash='0000000000000000'")
        self.store.db.executemany('INSERT INTO hash_bands VALUES(?,?,1)',list(bands('0000000000000000')))
        self.assertEqual(near_match(self.store.db,'000000000000000f',4),1)
        self.assertIsNone(near_match(self.store.db,'000000000000001f',4))

    def test_download_dedup_resume_review_export(self):
        self.seed(['https://example.com/a','https://example.com/b'])
        self.execute(lambda req:httpx.Response(200,content=picture()))
        self.assertEqual(self.store.stats()['images'],{'duplicate':1,'ready':1})
        self.execute(lambda req:self.fail('Completed images must not be requested again'))
        output=Path(self.tmp.name)/'manifest.jsonl'
        self.store.export(output)
        self.assertEqual(output.read_text(),'')
        review_page(self.store,Path(self.tmp.name)/'review.html',20)
        self.assertIn('data:image/jpeg;base64,',(Path(self.tmp.name)/'review.html').read_text(encoding='utf-8'))
        self.store.db.execute("UPDATE images SET review='accept' WHERE status='ready'")
        self.store.export(output)
        item=json.loads(output.read_text(encoding='utf-8'))
        self.assertTrue(Path(item['path']).exists())
        self.assertNotIn('url',item)

    def test_stop_on_rate_limit(self):
        self.seed(['https://example.com/a'])
        with self.assertRaises(AccessStopped):
            self.execute(lambda req:httpx.Response(429))
        self.assertEqual(self.store.stats()['images'],{'error':1})

    def test_target_is_not_exceeded(self):
        self.cfg['target']=1
        self.seed(['https://example.com/a','https://example.com/b'])
        self.execute(lambda req:httpx.Response(200,content=picture()))
        self.assertEqual(self.store.stats()['images'],{'pending':1,'ready':1})

    def test_near_duplicate_keeps_file(self):
        self.seed(['https://example.com/a','https://example.com/b'])
        self.execute(lambda req:httpx.Response(200,content=picture(color='red' if req.url.path=='/a' else 'blue')))
        self.assertEqual(self.store.stats()['images'],{'near_duplicate':1,'ready':1})
        row=self.store.db.execute("SELECT path FROM images WHERE status='near_duplicate'").fetchone()
        self.assertTrue((self.store.root/row['path']).exists())


if __name__ == '__main__':
    unittest.main()
