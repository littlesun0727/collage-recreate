"""Audited reference-image adapter; network is confined to the shared audit transport."""
import math
from pathlib import Path
import shutil
import subprocess
import time
from PIL import Image
from .common import BuildError, read_json, save, sha
from .providers import generation_prompt, process_generated
from .matting import require_backend
from .content_scope import resolve_scope
from .recipes import generated_text_metadata

CREDENTIALS = Path('D:/codes/yibu_credentials.local.json')
ADAPTER = Path(__file__).resolve().parents[2] / 'model_api/image_request.mjs'

def nearest_ratio(size):
    ratios = ['1:1','2:3','3:2','3:4','4:3','4:5','5:4','9:16','16:9','21:9']
    target = size[0] / size[1]
    return min(ratios, key=lambda r: abs(math.log((int(r.split(':')[0])/int(r.split(':')[1]))/target)))

def generate_yibu(entry, recipe, inputs, folder, args):
    if recipe['background']=='key': require_backend(recipe['key_color'])
    node = shutil.which('node')
    if not node: raise BuildError('missing_runtime', 'Node.js is required for the shared yibu adapter')
    folder.mkdir(parents=True, exist_ok=False)
    reference = folder/'reference.png'
    inputs['reference'].crop(entry['reference_box']).convert('RGB').save(reference)
    scope = entry.get('content_scope') or resolve_scope(entry,inputs)
    lettering = {'generated_text':generated_text_metadata(entry)} if entry.get('type')=='text' else {}
    prompt = generation_prompt(entry,recipe,scope,lettering)
    prompt += '\nKeep the full reference crop coordinate layout: identical relative positions, scale and margins. Do not center, zoom, rearrange or crop the requested elements. Remove nonmembers; keep their space empty.'
    save(folder/'content-scope.json',scope)
    (folder/'prompt.txt').write_text(prompt,encoding='utf-8')
    model = getattr(args,'image_model',None) or 'gemini-3-pro-image'
    credentials = getattr(args,'credentials',None) or CREDENTIALS
    timeout = getattr(args,'generation_timeout',600)
    adapter = ADAPTER.with_name('qwen_image_request.mjs') if model.startswith('qwen-image-') else ADAPTER
    audit = {'provider':'yibuapi','requested_model':model,'model_submissions':0,'automatic_retries':0,
             'reference_sha256':sha(reference),'status':'prepared','requested_size':list(entry['size'])}
    save(folder/'call.json',audit)
    started=time.monotonic()
    cmd=[node,str(adapter),'--image',str(reference),'--prompt',str(folder/'prompt.txt'),
         '--output',str(folder/'request'),'--credentials',str(credentials),'--model',model,
         '--ratio',nearest_ratio(entry['size']),'--timeout-seconds',str(timeout)]
    try:
        with (folder/'stdout.txt').open('wb') as out, (folder/'stderr.txt').open('wb') as err:
            proc=subprocess.run(cmd,stdout=out,stderr=err,timeout=timeout+30,
                                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        call=read_json(folder/'request/call.json')
        if call['status']=='image_url_returned':
            from .artifact_download import download_artifact
            call=download_artifact(folder/'request')
        audit.update(call,model_submissions=call.get('http_dispatches',0))
        save(folder/'call.json',audit)
        if proc.returncode or call['status']!='completed':
            raise BuildError('generation_failed','Image request failed; inspect request/call.json')
        with Image.open(folder/'request/original-image.bin') as im:
            im.load();generated=im.convert('RGBA')
        generated.save(folder/'original.png')
        audit.update(status='processing',generation_status='completed',raw_sha256=sha(folder/'original.png'),
                     actual_size=list(generated.size))
        save(folder/'call.json',audit)
        image,masks,processing,geometry=process_generated(generated,recipe,entry['size'])
        audit.update(status='completed',processing_status='completed',processing=processing,elapsed_seconds=time.monotonic()-started)
        save(folder/'call.json',audit)
        return image,masks,{'provider':'yibuapi','source':audit,'geometry':geometry,**lettering}
    except Exception as exc:
        request_audit=folder/'request/call.json'
        if request_audit.is_file():
            call=read_json(request_audit)
            audit.update(call,model_submissions=call.get('http_dispatches',0))
        audit.update(status='failed',error_type=type(exc).__name__,elapsed_seconds=time.monotonic()-started)
        save(folder/'call.json',audit)
        raise BuildError('generation_failed','Image call or processing failed; inspect call.json and request/. No automatic retry.') from None
