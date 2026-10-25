"""One external storage root for datasets, browser state and generated artifacts."""
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEFAULT_STORAGE = Path('E:/datas/collage-recreate/template_download') if os.name == 'nt' else Path.home()/'datas/collage-recreate/template_download'
STORAGE_ROOT = Path(os.environ.get('COLLAGE_DOWNLOAD_HOME', str(DEFAULT_STORAGE))).expanduser().resolve()
DATA_ROOT = STORAGE_ROOT/'data'
DEFAULT_DATASET = DATA_ROOT/'pipeline-1000'
