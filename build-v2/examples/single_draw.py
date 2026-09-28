"""Callable free-form drawing example: no shape JSON required."""
import math
from PIL import Image, ImageDraw


def curved_line(size, control_points, width, color):
    image=Image.new('RGBA',tuple(size)); draw=ImageDraw.Draw(image)
    p0,p1,p2=control_points
    points=[]
    for n in range(101):
        t=n/100
        points.append(tuple((1-t)**2*a+2*(1-t)*t*b+t*t*c for a,b,c in zip(p0,p1,p2)))
    draw.line(points,fill=color,width=max(1, math.ceil(width)),joint='curve')
    return image
