"""Convert MediaCrawler note exports to our independent image catalog format."""
import json
import re
from pathlib import Path


def normalize_note(record):
    if not isinstance(record, dict):
        raise ValueError('Expected a note object')
    ident = str(record.get('note_id') or record.get('noteId') or '')
    if not re.fullmatch(r'[a-fA-F0-9]{24}',ident):
        raise ValueError('Missing or invalid note_id')
    images = record.get('image_list',record.get('imageList',[])) or []
    if isinstance(images,str):
        # Upstream joins URLs with commas; commas inside a URL are retained.
        if images.lstrip().startswith('['):
            images = json.loads(images)
        else:
            images = re.split(r',\s*(?=https?://)',images)
    if not isinstance(images,list):
        raise ValueError('image_list must be a list or serialized URL list')
    keyword = record.get('source_keyword') or record.get('keyword') or 'mediacrawler'
    return {
        'note_id':ident, 'type':record.get('type','normal'),
        'title':record.get('title') or record.get('display_title') or '',
        'image_list':images,'xsec_token':record.get('xsec_token',''),
        'source_keyword':keyword,
    }


def import_export(store, path, recover_partial=False):
    path = Path(path)
    with path.open(encoding='utf-8-sig') as file:
        def lines():
            for line in file:
                if not line.strip():continue
                try:yield json.loads(line)
                except json.JSONDecodeError:
                    # Only tolerate a killed writer's unterminated final record.
                    if recover_partial and not line.endswith('\n') and not file.read():return
                    raise
        records = json.load(file) if path.suffix.lower() == '.json' else lines()
        if isinstance(records,dict):
            records = [records]
        imported = skipped = 0
        for number, record in enumerate(records,1):
            try:
                note = normalize_note(record)
            except (ValueError,TypeError) as exc:
                raise ValueError(f'Invalid MediaCrawler record {number}: {exc}') from exc
            count = store.ingest(note,note['source_keyword'])
            imported += count
            skipped += not count
    return {'imported_notes':imported,'skipped_records':skipped}
