"""Package only portable reports, diagrams, and their reproducible build sources."""
from pathlib import Path
import hashlib
import json
import zipfile
from datetime import datetime

HERE=Path(__file__).resolve().parent
FILES=['start.html','index.html','collection.html','decomposition.html',
       'workflow-overview.svg','workflow-overview.png','collection-workflow.svg',
       'collection-workflow.png','decomposition-workflow.svg','decomposition-workflow.png',
       'workflow-data.json','project-flows.json','READ-ME.txt','verification.json',
       'build_atlas.py','build_project_flows.py','verify_project_flows.py','package_atlas.py',
       'verify_atlas.py','visual_overview.py','atlas.css','atlas.js','atlas.template.html']


def main():
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
    target=HERE.parent/('workflow-atlas-offline-'+stamp+'.zip')
    files=[HERE/f for f in FILES]+sorted((HERE/'assets').rglob('*'))
    files=[f for f in files if f.is_file()]
    manifest={str(f.relative_to(HERE)).replace('\\','/'):hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for f in files:z.write(f,'workflow-atlas/'+str(f.relative_to(HERE)).replace('\\','/'))
        z.writestr('workflow-atlas/SHA256SUMS.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(target) as z:
        assert z.testzip() is None
        for name,digest in manifest.items():assert hashlib.sha256(z.read('workflow-atlas/'+name)).hexdigest()==digest
    digest=hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.zip.sha256').write_text(digest+'  '+target.name+'\n',encoding='ascii')
    print(json.dumps({'zip':str(target),'bytes':target.stat().st_size,'sha256':digest,'files':len(manifest)+1}))


if __name__=='__main__':main()
