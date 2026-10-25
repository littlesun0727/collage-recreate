import json
import tempfile
import unittest
from pathlib import Path

from mediacrawler_adapter import normalize_note, import_export
from mediacrawler_worker import classify_exception, StopAccess
from store import Store

IDENT = '0123456789abcdef01234567'


class AdapterTests(unittest.TestCase):
    def test_comma_inside_cdn_url_is_not_split(self):
        row=normalize_note({'note_id':IDENT,'image_list':'https://example.com/a?resize=100,200,https://example.com/b'})
        self.assertEqual(row['image_list'],['https://example.com/a?resize=100,200','https://example.com/b'])

    def test_native_array_and_serialized_array(self):
        for images in ([{'url_default':'https://example.com/a'}],'["https://example.com/a"]'):
            self.assertEqual(len(normalize_note({'note_id':IDENT,'image_list':images})['image_list']),1)

    def test_invalid_record_is_visible(self):
        with self.assertRaises(ValueError):
            normalize_note({'note_id':'invalid'})

    def test_import_keywords_and_idempotence(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory)
            try:
                path=Path(directory)/'export.jsonl'
                row={'note_id':IDENT,'source_keyword':'醒图拼贴模板','image_list':'https://example.com/a,https://example.com/b'}
                path.write_text(json.dumps(row)+'\n',encoding='utf-8')
                self.assertEqual(import_export(store,path)['imported_notes'],1)
                import_export(store,path)
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM images').fetchone()[0],2)
                self.assertEqual(store.db.execute('SELECT keyword FROM notes').fetchone()[0],'醒图拼贴模板')
            finally:
                store.db.close()

    def test_exception_report_does_not_leak_url(self):
        status,reason=classify_exception(RuntimeError('https://example.com/?cookie=secret'))
        self.assertEqual((status,reason),('error','RuntimeError'))
        self.assertEqual(classify_exception(StopAccess('Browser security restriction: 300012'))[0],'blocked')


if __name__=='__main__':
    unittest.main()
