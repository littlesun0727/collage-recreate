import asyncio
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

from browser import AccessStopped
from pipeline import candidates, review_cycle
from stage_cli import stage_lock, run_review, run_screen
from stage_media import collect_media, screen_local, sync_raw_assets, raw_complete_count
from store import Store


def photo(size=(900,1200),color='blue'):
    out=io.BytesIO();Image.new('RGB',size,color).save(out,format='PNG');return out.getvalue()


def box(kind,data):return (len(data)+8).to_bytes(4,'big')+kind+data
MP4=box(b'ftyp',b'isom\0\0\0\0mp42')+box(b'moov',b'fixture vide')+box(b'mdat',b'data')


class StageIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(self.temp.name)
        self.cfg={'target':1000,'download_concurrency':2,'download_delay_seconds':0,'max_attempts':2,
                  'max_image_bytes':30000000,'min_short_edge':720,'min_pixels':800000,
                  'max_aspect_ratio':3,'near_duplicate_distance':4}
    def tearDown(self):self.store.db.close();self.temp.cleanup()
    def ingest(self,images):self.store.ingest({'note_id':'e'*24,'image_list':images})
    def collect(self,responder,limit=1000):
        factory=httpx.AsyncClient
        with patch('stage_media.httpx.AsyncClient',return_value=factory(transport=httpx.MockTransport(responder))):
            return asyncio.run(collect_media(self.store,self.cfg,limit))

    def test_collection_does_not_decode_filter_or_invoke_vlm(self):
        self.ingest(['https://example.com/a'])
        with patch('stage_media.inspect_image',side_effect=AssertionError('screening in collector')):
            self.collect(lambda req:httpx.Response(200,content=b'not an image'))
        row=self.store.db.execute('SELECT * FROM images').fetchone()
        self.assertEqual(row['status'],'downloaded');self.assertIsNone(row['path'])
        raw=self.store.db.execute("SELECT * FROM raw_assets WHERE kind='cover'").fetchone()
        self.assertEqual((self.store.root/raw['path']).read_bytes(),b'not an image')
        self.assertEqual(candidates(self.store),[])
        with patch('httpx.AsyncClient',side_effect=AssertionError('network from screen')):
            self.assertEqual(screen_local(self.store,self.cfg),1)
        self.assertEqual(self.store.db.execute('SELECT status FROM images').fetchone()[0],'rejected')
        self.assertEqual(screen_local(self.store,self.cfg),0)

    def test_complete_file_then_offline_screen_and_idempotent_resume(self):
        self.ingest(['https://example.com/a','https://example.com/b'])
        self.collect(lambda req:httpx.Response(200,content=photo()))
        self.assertEqual(raw_complete_count(self.store),2)
        with patch('httpx.AsyncClient',side_effect=AssertionError('network from screen')):
            self.assertEqual(screen_local(self.store,self.cfg),2)
        self.assertEqual([r[0] for r in self.store.db.execute('SELECT status FROM images ORDER BY id')],['ready','duplicate'])
        self.assertEqual(len(candidates(self.store)),1)
        self.assertEqual(self.collect(lambda req:(_ for _ in ()).throw(AssertionError('redownload'))),0)
        self.assertEqual(screen_local(self.store,self.cfg),0)

    def test_access_block_does_not_prevent_offline_screening_other_files(self):
        self.ingest(['https://example.com/a','https://example.com/b'])
        with self.assertRaises(AccessStopped):
            self.collect(lambda req:httpx.Response(200,content=photo()) if req.url.path=='/a' else httpx.Response(461))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM raw_assets WHERE status='blocked'").fetchone()[0],1)
        self.assertEqual(screen_local(self.store,self.cfg),1)
        self.assertEqual(len(candidates(self.store)),1)

    def test_live_pair_waits_for_local_mp4_then_screens(self):
        self.ingest([{'url_default':'https://example.com/cover','live_photo':True,
                      'stream':{'h264':[{'master_url':'https://example.com/video'}]}}])
        self.collect(lambda req:httpx.Response(200,content=photo()),limit=1)
        self.assertEqual(screen_local(self.store,self.cfg),0)
        self.assertEqual(candidates(self.store),[])
        self.collect(lambda req:httpx.Response(200,content=MP4))
        self.assertEqual(raw_complete_count(self.store),1)
        self.assertEqual(screen_local(self.store,self.cfg),1)
        self.assertEqual(len(candidates(self.store)),1)
        self.assertEqual(self.store.db.execute('SELECT status FROM live_assets').fetchone()[0],'ready')

    def test_live_motion_difference_is_not_cover_duplicate(self):
        self.ingest([{'url_default':'https://example.com/cover','live_photo':True,
            'stream':{'h264':[{'master_url':f'https://example.com/video{i}'}]}} for i in range(3)])
        def respond(req):
            data=photo() if req.url.path=='/cover' else MP4+(box(b'free',b'different') if req.url.path=='/video1' else b'')
            return httpx.Response(200,content=data)
        self.collect(respond);screen_local(self.store,self.cfg)
        self.assertEqual([r[0] for r in self.store.db.execute('SELECT status FROM images ORDER BY id')],['ready','ready','duplicate'])

    def test_target_and_corruption(self):
        self.ingest(['https://example.com/a','https://example.com/b']);self.cfg['target']=1
        self.collect(lambda req:httpx.Response(200,content=photo()))
        self.assertEqual(raw_complete_count(self.store),1)
        raw=self.store.db.execute("SELECT path FROM raw_assets WHERE status='ready'").fetchone()[0]
        (self.store.root/raw).write_bytes(b'truncated')
        screen_local(self.store,self.cfg)
        self.assertEqual(self.store.db.execute('SELECT status FROM images WHERE id=1').fetchone()[0],'rejected')

    def test_legacy_decisions_preserved_and_partial_pair_recovered(self):
        self.ingest(['https://example.com/a'])
        (self.store.root/'existing.png').write_bytes(photo())
        self.store.db.execute("UPDATE images SET status='ready',path='existing.png',review='accept'");self.store.db.commit()
        sync_raw_assets(self.store);screen_local(self.store,self.cfg)
        self.assertEqual(self.store.db.execute('SELECT review FROM images').fetchone()[0],'accept')
        self.assertEqual(raw_complete_count(self.store),1)

    def test_stage_locks_are_independent_and_same_stage_exclusive(self):
        with stage_lock(self.store.root,'screen'),stage_lock(self.store.root,'review'):
            with self.assertRaises(RuntimeError):
                with stage_lock(self.store.root,'screen'):pass

    def test_review_has_no_network_when_queue_under_100(self):
        args=SimpleNamespace(flush=False,watch=False,model='fixture',concurrency=2,cooldown=0)
        with patch('vlm_batch.CodexServer',side_effect=AssertionError('unneeded model login')):
            self.assertEqual(run_review(self.store,self.cfg,args),0)

    def test_vlm_partial_failure_resumes_cached_calls_only(self):
        self.ingest([f'https://example.com/{i}' for i in range(21)])
        (self.store.root/'existing.png').write_bytes(photo())
        self.store.db.execute("UPDATE images SET status='ready',path='existing.png'");self.store.db.commit()
        calls=[]
        class Fake:
            fail=True
            def triage(self,rows,model,effort):
                calls.append(len(rows))
                if self.fail and len(rows)==1:raise RuntimeError('simulated model outage')
                return {'items':[{'id':str(r['id']),'label':'single','needs_crop':False} for r in rows]}
            def locate(self,*args):raise AssertionError('no crops expected')
        fake=Fake()
        with patch('pipeline.random.SystemRandom.shuffle',lambda self,rows:None):
            with self.assertRaises(RuntimeError):review_cycle(self.store,fake,'fixture',21,2,self.cfg)
            self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM review_cache').fetchone()[0],20)
            self.assertEqual(len(candidates(self.store)),21)
            fake.fail=False;review_cycle(self.store,fake,'fixture',21,2,self.cfg)
        self.assertEqual(sorted(calls),[1,1,20])
        self.assertEqual(len(candidates(self.store)),0)


if __name__=='__main__':unittest.main()
