"""Customer RGB always comes from a pinned source. Masks are never image substitutes."""
import math
from functools import lru_cache
from pathlib import Path
import numpy as np
from PIL import Image, ImageOps, ImageFilter, ImageColor
from common import sha, fingerprint, save, read, verify_source
from effects import clip_round, expanded_outline, photo_layout, photo_card


def fit_image(im, size, center=(.5,.5), contain=False):
    if contain:
        tile=ImageOps.contain(im,size,Image.Resampling.LANCZOS)
        result=Image.new('RGBA',size);result.alpha_composite(tile,((size[0]-tile.width)//2,(size[1]-tile.height)//2));return result
    return ImageOps.fit(im,size,Image.Resampling.LANCZOS,centering=tuple(center))


def feather_alpha(size, amount):
    w,h=size;y,x=np.mgrid[0:h,0:w].astype(np.float32)
    distance=np.minimum.reduce([x+.5,w-x-.5,y+.5,h-y-.5])
    v=np.clip(distance/max(1,amount*min(size)),0,1);v=v*v*(3-2*v)
    return Image.fromarray(np.rint(v*255).astype('uint8'))


@lru_cache(maxsize=2)
def session(model, model_sha):
    import onnxruntime as ort
    opts=ort.SessionOptions();opts.intra_op_num_threads=4;opts.inter_op_num_threads=1
    return ort.InferenceSession(model,sess_options=opts,providers=['CPUExecutionProvider'])


def cutout(im, source, model, cache):
    if not model or not Path(model).is_file(): raise ValueError('cutout needs existing BiRefNet FP32 ONNX weights (--cutout-model in prepare)')
    model_sha=sha(model);key=fingerprint([source['sha256'],model_sha,'v5-birefnet-1'])
    path=Path(cache)/(key+'.png');manifest=path.with_suffix('.json')
    if path.exists() and manifest.exists() and sha(path)==read(manifest)['sha256']:
        with Image.open(path) as raw:return raw.convert('RGBA')
    engine=session(str(model),model_sha);inp=engine.get_inputs()[0]
    if inp.type!='tensor(float)':raise ValueError('BiRefNet weights must be FP32')
    h,w=[v if isinstance(v,int) and v>0 else 1024 for v in inp.shape[2:]]
    tensor=np.asarray(im.convert('RGB').resize((w,h)),dtype=np.float32)/255
    tensor=(tensor-np.array([.485,.456,.406],np.float32))/np.array([.229,.224,.225],np.float32)
    alpha=np.asarray(engine.run(None,{inp.name:np.ascontiguousarray(tensor.transpose(2,0,1)[None])})[-1]).squeeze()
    if alpha.ndim!=2 or not np.isfinite(alpha).all():raise ValueError('Invalid segmentation output')
    if alpha.min()<0 or alpha.max()>1:alpha=1/(1+np.exp(-np.clip(alpha,-50,50)))
    alpha=Image.fromarray(np.clip(alpha,0,1).astype('float32')).resize(im.size,Image.Resampling.BILINEAR)
    a=np.rint(np.clip(np.asarray(alpha),0,1)*255).astype('uint8')
    a=np.rint(a.astype('float32')*np.asarray(im.getchannel('A'))/255).astype('uint8')
    fg=im.copy();fg.putalpha(Image.fromarray(a))
    bounds=fg.getchannel('A').point(lambda x:255 if x>8 else 0).getbbox()
    if not bounds:raise ValueError('Empty customer cutout')
    path.parent.mkdir(exist_ok=True,parents=True);fg.save(path);save(manifest,{'sha256':sha(path),'source_sha256':source['sha256'],'model_sha256':model_sha})
    return fg


def make_photo(obj, source, binding, size, run, model):
    path=verify_source(source)
    with Image.open(path) as raw: im=ImageOps.exif_transpose(raw).convert('RGBA')
    if obj['mode']=='cutout':
        im=cutout(im,source,model,Path(run)/'assets/photos/.cache')
        bounds=im.getchannel('A').point(lambda x:255 if x>8 else 0).getbbox();im=im.crop(bounds)
    crop=binding.get('source_crop',[0,0,1,1]);w,h=im.size
    im=im.crop((int(crop[0]*w),int(crop[1]*h),max(1,int(crop[2]*w)),max(1,int(crop[3]*h))))
    if binding.get('mirror_x'):im=ImageOps.mirror(im)
    style=obj.get('style',{})
    left,top,right,bottom=photo_layout(size,style)
    window=(right-left,bottom-top)
    im=fit_image(im,window,binding.get('crop_center',[.5,.5]),obj['mode']=='cutout')
    if style.get('corner_radius',0):im=clip_round(im,style['corner_radius'])
    if obj['mode']=='feather':
        a=np.asarray(im.getchannel('A'),dtype='float32')*np.asarray(feather_alpha(window,style.get('feather',.08)))/255
        im.putalpha(Image.fromarray(np.rint(a).astype('uint8')))
    # Keep actual customer pixels distinct from paper and outlines for visibility.
    content_alpha=Image.new('L',size);content_alpha.paste(im.getchannel('A'),(left,top))
    im=photo_card(im,size,style)
    im,pad=expanded_outline(im,style.get('outline_width',3 if obj['mode']=='cutout' else 0),style.get('outline_color','#FFFFFF'))
    if pad:
        mask=Image.new('L',im.size);mask.paste(content_alpha,(pad,pad));content_alpha=mask
    if not im.getchannel('A').getbbox():raise ValueError('Photo output is entirely transparent')
    content_file=Path(run)/'assets/photos'/(obj['id']+'-content-mask.png');content_file.parent.mkdir(parents=True,exist_ok=True);content_alpha.save(content_file)
    return im,{'resource_type':'customer_photo','source':source,'mode':obj['mode'],'binding':binding,'pixel_operation':'bound-source-resize-crop-mask; no generated RGB','content_mask':str(content_file),'content_mask_sha256':sha(content_file)}
