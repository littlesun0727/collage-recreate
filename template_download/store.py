import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, urlencode
from live_media import live_asset


def note_id(url):
    match = re.search(r'/(?:explore|search_result)/([a-fA-F0-9]{24})(?:[/?#]|$)', url)
    return match.group(1) if match else None


def image_url(item):
    if isinstance(item, str):
        value = item
    else:
        info = item.get('info_list', item.get('infoList', [])) or []
        preferred = next((x.get('url') for x in info if x.get('image_scene', x.get('imageScene')) == 'WB_DFT'), None)
        value = item.get('url_default') or item.get('urlDefault') or preferred or item.get('url') or ''
    if value.startswith('//'):
        value = 'https:' + value
    return value if urlsplit(value).scheme in ('https', 'http') else ''


def extract_notes(value):
    """Accept snake/camel case browser payloads; do not mistake search covers for galleries."""
    if isinstance(value, dict):
        ident = value.get('note_id') or value.get('noteId') or value.get('id')
        imgs = value.get('image_list', value.get('imageList', [] if value.get('type') == 'video' else None))
        if ident and isinstance(imgs, list) and re.fullmatch(r'[a-fA-F0-9]{24}', str(ident)):
            yield str(ident), value, [u for x in imgs if (u := image_url(x))]
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from extract_notes(child)
    elif isinstance(value, list):
        for child in value:
            yield from extract_notes(child)


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'catalog.sqlite', timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS notes(
            id TEXT PRIMARY KEY, url TEXT NOT NULL, keyword TEXT, title TEXT DEFAULT '',
            status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0, error TEXT);
        CREATE TABLE IF NOT EXISTS sources(note_id TEXT, keyword TEXT, PRIMARY KEY(note_id,keyword));
        CREATE TABLE IF NOT EXISTS images(
            id INTEGER PRIMARY KEY, note_id TEXT, position INTEGER, url TEXT,
            status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0, error TEXT,
            sha256 TEXT, dhash TEXT, width INTEGER, height INTEGER, path TEXT,
            duplicate_of INTEGER, review TEXT DEFAULT 'pending', UNIQUE(note_id,position));
        CREATE INDEX IF NOT EXISTS images_queue ON images(status,attempts,id);
        CREATE INDEX IF NOT EXISTS images_sha ON images(sha256);
        CREATE TABLE IF NOT EXISTS hash_bands(band INTEGER, value TEXT, image_id INTEGER,
            PRIMARY KEY(band,value,image_id));
        CREATE TABLE IF NOT EXISTS searches(keyword TEXT PRIMARY KEY, runs INTEGER DEFAULT 0,
            updated TEXT, discovered INTEGER DEFAULT 0);
        ''')
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(images)')}
        if 'media_kind' not in columns:
            self.db.execute("ALTER TABLE images ADD COLUMN media_kind TEXT DEFAULT 'image'")
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS live_assets(
            image_id INTEGER PRIMARY KEY, url TEXT, status TEXT DEFAULT 'pending',
            attempts INTEGER DEFAULT 0, error TEXT, path TEXT, sha256 TEXT, bytes INTEGER);
        CREATE TABLE IF NOT EXISTS vlm_results(
            image_id INTEGER PRIMARY KEY, label TEXT, model TEXT, effort TEXT,
            prompt_sha TEXT, report TEXT, updated TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS pipeline_state(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS processed_assets(
            image_id INTEGER PRIMARY KEY, action TEXT, bbox TEXT, reason TEXT,
            path TEXT, sha256 TEXT, pixel_sha256 TEXT, dhash TEXT, width INTEGER, height INTEGER,
            status TEXT, duplicate_of INTEGER, version TEXT);
        CREATE INDEX IF NOT EXISTS processed_pixels ON processed_assets(pixel_sha256);
        CREATE TABLE IF NOT EXISTS processed_bands(band INTEGER,value TEXT,image_id INTEGER,
            PRIMARY KEY(band,value,image_id));
        CREATE TABLE IF NOT EXISTS raw_assets(
            image_id INTEGER, kind TEXT, status TEXT DEFAULT 'pending', path TEXT,
            sha256 TEXT, bytes INTEGER, attempts INTEGER DEFAULT 0, error TEXT,
            updated TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(image_id,kind));
        CREATE TABLE IF NOT EXISTS review_cache(
            image_id INTEGER, stage TEXT, cache_key TEXT, payload TEXT, report TEXT,
            updated TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(image_id,stage,cache_key));
        CREATE TABLE IF NOT EXISTS stage_runs(
            id INTEGER PRIMARY KEY, stage TEXT, state TEXT, started TEXT DEFAULT CURRENT_TIMESTAMP,
            finished TEXT, error TEXT);
        ''')

    def discover(self, url, keyword):
        if urlsplit(url).hostname != 'www.xiaohongshu.com':
            return
        ident = note_id(url)
        if not ident:
            return
        self.db.execute('INSERT OR IGNORE INTO notes(id,url,keyword) VALUES(?,?,?)', (ident,url,keyword))
        # Search tokens expire: refresh the URL on rediscovery.
        if parse_qs(urlsplit(url).query).get('xsec_token'):
            self.db.execute('UPDATE notes SET url=? WHERE id=?', (url,ident))
        self.db.execute('INSERT OR IGNORE INTO sources VALUES(?,?)', (ident,keyword))
        self.db.commit()

    def ingest(self, payload, keyword='import'):
        count = 0
        for ident, card, urls in extract_notes(payload):
            if card.get('type') == 'video':
                self.discover('https://www.xiaohongshu.com/explore/' + ident, keyword)
                self.db.execute("UPDATE notes SET status='excluded_video',error='video_note' WHERE id=?", (ident,))
                self.db.execute("UPDATE images SET status='rejected',review='reject',error='video_note' WHERE note_id=?", (ident,))
                self.db.commit()
                continue
            if not urls:
                continue
            token = card.get('xsec_token', card.get('xsecToken', ''))
            url = 'https://www.xiaohongshu.com/explore/' + ident
            if token:
                url += '?' + urlencode({'xsec_token':token, 'xsec_source':'pc_search'})
            self.discover(url, keyword)
            self.db.execute("UPDATE notes SET title=?,status='done',error=NULL WHERE id=?",
                            (card.get('title') or card.get('display_title') or '', ident))
            for pos, raw in enumerate(card.get('image_list', card.get('imageList', []))):
                image = image_url(raw)
                if not image:
                    continue
                self.db.execute('''INSERT INTO images(note_id,position,url) VALUES(?,?,?)
                    ON CONFLICT(note_id,position) DO UPDATE SET url=excluded.url''', (ident,pos,image))
                is_live, video_url = live_asset(raw)
                if is_live:
                    image_id = self.db.execute('SELECT id FROM images WHERE note_id=? AND position=?', (ident,pos)).fetchone()[0]
                    self.db.execute("UPDATE images SET media_kind='live' WHERE id=?", (image_id,))
                    self.db.execute('''INSERT INTO live_assets(image_id,url,status,error) VALUES(?,?,?,?)
                        ON CONFLICT(image_id) DO UPDATE SET url=CASE WHEN excluded.url!='' THEN excluded.url ELSE live_assets.url END,
                        status=CASE WHEN live_assets.status='missing_url' AND excluded.url!='' THEN 'pending' ELSE live_assets.status END''',
                        (image_id,video_url,'pending' if video_url else 'missing_url',None if video_url else 'live_stream_unavailable'))
            count += 1
        self.db.commit()
        return count

    def stats(self):
        return {table: dict(self.db.execute(f'SELECT status,COUNT(*) FROM {table} GROUP BY status').fetchall())
                for table in ('notes','images')} | {'accepted': self.db.execute(
                    "SELECT COUNT(*) FROM images WHERE status='ready' AND review='accept'").fetchone()[0]}

    def export(self, output, candidates=False):
        where = "i.status='ready'" + ('' if candidates else " AND i.review='accept'")
        query = f'''SELECT i.*,n.title,n.keyword,n.id AS source_note FROM images i
            JOIN notes n ON n.id=i.note_id WHERE {where} ORDER BY i.id'''
        with Path(output).open('w',encoding='utf-8') as file:
            for row in self.db.execute(query):
                item = dict(row)
                item.pop('url', None)  # Signed image URLs are local operational data.
                if item['media_kind'] == 'live':
                    asset = self.db.execute('SELECT status,path,sha256,bytes FROM live_assets WHERE image_id=?', (item['id'],)).fetchone()
                    item['live_video'] = dict(asset) if asset else None
                    if not candidates and (not asset or asset['status'] != 'ready'):
                        continue
                    if asset and asset['path']:
                        item['live_video']['path'] = str((self.root / asset['path']).resolve())
                item['source_url'] = 'https://www.xiaohongshu.com/explore/' + item.pop('source_note')
                item['path'] = str((self.root / item['path']).resolve())
                processed = self.db.execute('SELECT * FROM processed_assets WHERE image_id=?', (item['id'],)).fetchone()
                if processed:
                    item['processing'] = dict(processed)
                    item['processing']['bbox'] = json.loads(processed['bbox']) if processed['bbox'] else None
                    if processed['path']:
                        item['processing']['path'] = str((self.root / processed['path']).resolve())
                    if not candidates:
                        if processed['status'] != 'ready':
                            continue
                        item['original'] = {k:item[k] for k in ('path','sha256','dhash','width','height')}
                        for key in ('sha256','dhash','width','height'):
                            item[key] = processed[key]
                        item['path'] = item['processing']['path']
                file.write(json.dumps(item,ensure_ascii=False) + '\n')
