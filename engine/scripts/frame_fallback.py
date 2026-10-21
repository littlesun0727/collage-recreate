"""A rejected extraction never removes the structural photo frame."""
from copy import deepcopy
from PIL import ImageChops
from overlays import draw_overlay


def draw_frame(obj, size, reference, photos):
    style=deepcopy(obj.get('style',{}))
    # With no explicit design colour, use a neutral paper placeholder. Sampling
    # bbox edges often measures the surrounding background rather than the frame.
    style.setdefault('fill','#F5F3EF')
    style['shape']='rounded_rectangle' if style.get('radius') else 'rectangle'
    style.pop('window',None)
    tile,_=draw_overlay({'kind':'overlay','method':'local','style':style},size,reference,photos)
    photo=next((p for p in photos if p['id']==obj['photo_id']),{})
    shape=photo.get('photo_window_spec',{}).get('shape',{})
    if shape.get('type') in {'star','heart','circle','ellipse','polygon'}:
        from photo_windows import shape_mask
        tile.putalpha(ImageChops.multiply(tile.getchannel('A'),shape_mask(size,shape)))
    # The renderer subtracts the actual shaped/rotated photo aperture in canvas
    # coordinates. This preserves asymmetric borders and the original pivot.
    return tile,{'quality':'approximate','method':'local_frame','fallback':True,
                 'photo_id':obj['photo_id'],'fill':style['fill'],
                 'note':'提取相框不可用，已按原外框与照片内口绘制替代相框；纹样细节为近似'}
