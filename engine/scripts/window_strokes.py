"""Dashed/offset contours on the actual window or customer subject mask."""
import math
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps, ImageChops
from overlays import stroke_path


def apply(im,spec,subject_alpha):
    from photo_windows import apply_window, shape_mask, fade_mask
    from copy import deepcopy
    base_spec=deepcopy(spec);base_spec['stroke'].update(enabled=False,width=0)
    base,content=apply_window(im,base_spec,subject_alpha)
    stroke=spec['stroke'];short=min(im.size);width=max(1,stroke['width']*short)
    offset=stroke.get('offset',0)*short;placement=stroke.get('placement','inside')
    distance={'inside':-offset-width/2,'center':0,'outside':offset+width/2}[placement]
    pad=math.ceil(max(0,distance)+width/2)+3
    shape=shape_mask(im.size,spec['shape'])
    if stroke['target']=='window':boundary=shape
    elif subject_alpha is not None:boundary=ImageChops.multiply(subject_alpha,shape)
    else:boundary=ImageChops.multiply(im.getchannel('A'),shape)
    mask=np.asarray(ImageOps.expand(boundary,pad,fill=0))>127
    inside=cv2.distanceTransform(mask.astype('uint8'),cv2.DIST_L2,5)
    outside=cv2.distanceTransform((~mask).astype('uint8'),cv2.DIST_L2,5)
    region=(outside-inside<=distance).astype('uint8')
    contours,_=cv2.findContours(region,cv2.RETR_LIST,cv2.CHAIN_APPROX_NONE)
    ink=Image.new('L',(im.width+2*pad,im.height+2*pad));draw=ImageDraw.Draw(ink)
    pattern=stroke.get('pattern','dash' if stroke.get('dash') else 'solid')
    dash=None if pattern=='solid' else [v*short for v in stroke.get('dash',[.025,.018])]
    if pattern=='dot':dash=[max(.1,width*.05),max(width*1.7,dash[1])]
    cap='round' if pattern=='dot' else stroke.get('line_cap','butt')
    for contour in contours:
        points=[tuple(map(float,p[0])) for p in contour]
        if len(points)>1:stroke_path(draw,points+[points[0]],255,max(1,round(width)),dash,cap)
    # Fade across a photo and its outline together. Extend boundary values into
    # the symmetric padding, preserving unfaded external strokes.
    fade=np.asarray(fade_mask(im.size,spec['fade']))
    fade=Image.fromarray(np.pad(fade,pad,mode='edge'))
    ink=ImageChops.multiply(ink,fade)
    out=ImageOps.expand(base,pad,fill=0);content=ImageOps.expand(content,pad,fill=0)
    colored=Image.new('RGBA',out.size,stroke['color']);colored.putalpha(ink)
    if placement=='outside' and stroke['target']=='subject' and spec['subject']['mode']=='cutout':
        # Contour rasterization may overlap the edge by a pixel. Put the sticker
        # backing underneath the cutout, preserving its RGB and content mask.
        return Image.alpha_composite(colored,out),content
    out=Image.alpha_composite(out,colored)
    return out,ImageChops.multiply(content,ImageChops.invert(ink))
