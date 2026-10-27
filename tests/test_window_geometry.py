import math
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from collage.workflow.window_geometry import CORNER_NAMES, fit_rectangle
from collage.workflow.stages.photo_windows import schema, compile_response
from collage.vlm.coordinates import ReferenceCoordinates
from photo_windows import defaults


def corners(angle, width=280, height=430):
    c,s=math.cos(math.radians(angle)),math.sin(math.radians(angle))
    return {k:[450+c*x-s*y,900+s*x+c*y] for k,(x,y) in zip(CORNER_NAMES,
        [(-width/2,-height/2),(width/2,-height/2),(width/2,height/2),(-width/2,height/2)])}


@pytest.mark.parametrize('angle',[0,8,-7.4,34,45,-90,125])
@pytest.mark.parametrize('mode',['pixel','normalized_1000'])
def test_rectangle_dimensions_and_angle_survive_non_square_reference(angle,mode):
    points=corners(angle)
    raw=deepcopy(points)
    if mode=='normalized_1000':
        raw={k:[x/900*1000,y/1800*1000] for k,(x,y) in points.items()}
    adapter=ReferenceCoordinates({'reference_size':[900,1800]},mode)
    decoded=adapter.decode({'coordinate_space':mode,'photos':[
        {'photo_id':'p','placement':{'type':'rectangle_corners','corners':raw}}]})
    placement,review=fit_rectangle(decoded['photos'][0]['placement']['corners'])
    assert placement['bbox']==[310,685,590,1115]
    assert placement['rotation']==pytest.approx(angle)
    assert not review['warnings']


@pytest.mark.parametrize('mutation',['crossed','duplicate','nonfinite','bool'])
def test_invalid_corners_rejected(mutation):
    points=corners(8)
    if mutation=='crossed':points['top_right'],points['bottom_right']=points['bottom_right'],points['top_right']
    elif mutation=='duplicate':points['top_right']=points['top_left']
    elif mutation=='nonfinite':points['top_left'][0]=float('nan')
    else:points['top_left'][0]=True
    with pytest.raises(ValueError):fit_rectangle(points)


def test_imperfect_rectangle_has_review_evidence():
    points=corners(0)
    points['top_left'][0]+=60
    _,review=fit_rectangle(points)
    assert review['warnings'] and review['relative_corner_error']>.05


@pytest.mark.parametrize('shape',['rectangle','rounded_rect','heart','ellipse','star','circle','polygon'])
def test_compilation_keeps_local_shapes_and_removes_model_only_geometry(shape):
    analysis={'reference_size':[900,1800],'objects':[
        {'id':'p','kind':'photo','bbox':[200,500,700,1400]}],'layer_order':['p']}
    answer=defaults(analysis)
    spec=answer['photos'][0]
    spec['reason']='test geometry'
    spec['mask']={'enabled':True,'shape':{'type':shape},'stroke':{'enabled':False,'width':0,
        'color':'#FFFFFF','pattern':'solid','line_cap':'butt','placement':'inside','offset':0}}
    if shape=='rounded_rect':spec['mask']['shape']['corner_radius']=.1
    if shape=='star':spec['mask']['shape'].update(star_points=5,inner_ratio=.5)
    if shape=='polygon':spec['mask']['shape']['points']=[[0,0],[1,0],[.5,1]]
    spec['placement']=({'type':'rectangle_corners','corners':corners(8)} if shape in {'rectangle','rounded_rect'}
                       else {'type':'box_rotation','bbox':[310,685,590,1115],'rotation':8})
    Draft202012Validator(schema()).validate(answer)
    value,plan,_=compile_response(answer,{'analysis':analysis,'text_units':[]})
    assert value['analysis']['objects'][0]['rotation']==8
    assert plan['photos'][0]['mask']['shape']==spec['mask']['shape']
    assert 'placement' not in plan['photos'][0]
    assert analysis['objects'][0]['bbox']==[200,500,700,1400]


def test_corner_bounds_checked_per_axis():
    adapter=ReferenceCoordinates({'reference_size':[900,1800]},'pixel')
    with pytest.raises(ValueError,match='Invalid global corner'):
        adapter.decode({'coordinate_space':'pixel','photos':[{'placement':{'corners':{'top_left':[901,20]}}}]})
