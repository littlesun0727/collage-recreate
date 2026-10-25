import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

from browser import AccessStopped
from live_download import download_live
from live_media import live_asset, validate_mp4
from pipeline import candidates, process_pending, review_cycle
from store import Store


def box(kind, content):
    return (len(content)+8).to_bytes(4,'big')+kind+content


MP4 = box(b'ftyp',b'isom\0\0\0\0mp42')+box(b'moov',b'fixture vide')+box(b'mdat',b'1234')


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def ingest_live(self, stream=True):
        image = {'url_default':'https://example.com/cover','live_photo':True}
        if stream:
            image['stream'] = {'h264':[{'master_url':'https://example.com/live.mp4','height':720}]}
        self.store.ingest({'note_id':'a'*24,'type':'normal','image_list':[image]})
        self.store.db.execute("UPDATE images SET status='ready',path='cover.jpg'")
        self.store.db.commit()

    def test_live_pair_and_incomplete_export(self):
        self.ingest_live()
        self.assertEqual(candidates(self.store),[])
        self.store.db.execute("UPDATE images SET review='accept'")
        out=Path(self.tmp.name)/'out.jsonl'
        self.store.export(out)
        self.assertEqual(out.read_text(),'')
        self.store.db.execute("UPDATE live_assets SET status='ready',path='motion.mp4'")
        self.store.export(out)
        row=json.loads(out.read_text())
        self.assertTrue(row['live_video']['path'].endswith('motion.mp4'))
        self.assertNotIn('url',row['live_video'])

    def test_video_note_excluded_without_images(self):
        self.store.ingest({'note_id':'b'*24,'type':'video'})
        self.assertEqual(self.store.db.execute('SELECT status FROM notes').fetchone()[0],'excluded_video')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM images').fetchone()[0],0)

    def test_missing_live_stream_not_silently_static(self):
        self.ingest_live(False)
        self.assertEqual(self.store.db.execute('SELECT status FROM live_assets').fetchone()[0],'missing_url')
        self.assertEqual(candidates(self.store),[])

    def test_camel_stream_and_invalid_mp4(self):
        self.assertEqual(live_asset({'livePhoto':True,'stream':{'h264':[{'masterUrl':'//example.com/v'}]}}),
                         (True,'https://example.com/v'))
        self.assertEqual(live_asset({'livePhoto':False}), (False,''))
        validate_mp4(MP4)
        for data in (b'<html>denied</html>',MP4[:-1],box(b'ftyp',b'qt  ')):
            with self.assertRaises(ValueError):
                validate_mp4(data)

    def test_live_download_saves_mp4_and_stops_403(self):
        self.ingest_live()
        real_client=httpx.AsyncClient
        def client(status, content):
            return real_client(transport=httpx.MockTransport(lambda request:httpx.Response(status,content=content)))
        cfg={'max_attempts':3,'download_delay_seconds':0}
        with patch('live_download.httpx.AsyncClient',return_value=client(200,MP4)):
            asyncio.run(download_live(self.store,cfg))
        asset=self.store.db.execute('SELECT * FROM live_assets').fetchone()
        self.assertEqual((self.store.root/asset['path']).read_bytes(),MP4)
        self.assertEqual(len(candidates(self.store)),1)
        self.store.db.execute("UPDATE live_assets SET status='pending'")
        with patch('live_download.httpx.AsyncClient',return_value=client(403,b'')):
            with self.assertRaises(AccessStopped):
                asyncio.run(download_live(self.store,cfg))

    def test_hundred_boundary_tail_and_resume(self):
        self.store.ingest({'note_id':'c'*24,'image_list':[f'https://example.com/{i}' for i in range(107)]})
        self.store.db.execute("UPDATE images SET status='ready',path='fixture.jpg'")
        self.store.db.commit()
        Image.new('RGB',(800,1100),'blue').save(self.store.root/'fixture.jpg')
        calls=[]
        class FakeVLM:
            def locate(self,*args):
                raise AssertionError("No crop candidates expected")
            def triage(self,rows,model,effort):
                calls.append(len(rows))
                return {'items':[{'id':str(r['id']),'label':'single' if r['id']%2 else 'collection',
                                 'needs_crop':False} for r in rows]}
        async def no_live(*args):
            pass
        with patch('pipeline.download_live',no_live):
            cfg=json.loads((Path(__file__).resolve().parents[1]/'config.json').read_text(encoding='utf-8'))
            process_pending(self.store,cfg,FakeVLM(),'fixture',2,0)
        self.assertEqual(sorted(calls),[20]*5)
        self.assertEqual(len(candidates(self.store)),7)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM vlm_results').fetchone()[0],100)
        review_cycle(self.store,FakeVLM(),'fixture',100,2)
        self.assertEqual(calls[-1],7)
        self.assertEqual(review_cycle(self.store,FakeVLM(),'fixture'),0)

    def test_live_dedup_requires_both_cover_and_motion(self):
        self.store.ingest({'note_id':'d'*24,'image_list':[
            {'url_default':'https://example.com/cover','live_photo':True,
             'stream':{'h264':[{'master_url':f'https://example.com/{i}'}]}} for i in range(3)]})
        self.store.db.execute("UPDATE images SET status='ready',path='cover.jpg',sha256='same-cover'")
        self.store.db.commit()
        real_client=httpx.AsyncClient
        def respond(request):
            data = MP4 if request.url.path in ('/0','/2') else MP4+box(b'free',b'different-motion-fixture')
            return httpx.Response(200,content=data)
        with patch('live_download.httpx.AsyncClient',return_value=real_client(transport=httpx.MockTransport(respond))):
            asyncio.run(download_live(self.store,{'max_attempts':3,'download_delay_seconds':0}))
        self.assertEqual([r[0] for r in self.store.db.execute('SELECT status FROM images ORDER BY id')],
                         ['ready','ready','duplicate'])


if __name__ == '__main__':
    unittest.main()
