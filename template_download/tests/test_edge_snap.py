import unittest
from PIL import Image, ImageDraw
from edge_snap import snap_box, ratio_tiebreak, refine_ratio, postprocess_box, COMMON_RATIOS


class EdgeSnapTests(unittest.TestCase):
    def test_ratio_adjusts_only_weak_edge_without_centering(self):
        result={'pixels':[100,100,700,910],'status':'review','edges':{
            k:{'status':'snapped','score':1} for k in ('left','top','right','bottom')}}
        result['edges']['bottom']={'status':'unresolved'}
        adjusted=refine_ratio(result,(1000,1200))
        self.assertEqual(adjusted['pixels'],[100,100,700,900])
        self.assertEqual(adjusted['status'],'review')
        result['pixels']=[100,100,700,800]
        self.assertEqual(refine_ratio(result,(1000,1200))['pixels'],result['pixels'])

    def test_landscape_and_portrait_ratios(self):
        self.assertEqual(set(COMMON_RATIOS),{'1:1','1:2','2:3','3:2','3:4','4:3','9:16','16:9'})
        result={'pixels':[100,100,910,700],'status':'resolved','edges':{
            k:{'status':'snapped','score':1} for k in ('left','top','right','bottom')}}
        result['edges']['right']['score']=.5
        adjusted=refine_ratio(result,(1200,1000))
        self.assertEqual(adjusted['pixels'],[100,100,900,700])
        self.assertEqual(adjusted['ratio_adjustment']['ratio'],'4:3')

    def test_expansion_skipped_without_trying_another_edge(self):
        for box,weak in [([100,100,700,890],'bottom'),([100,100,890,700],'right'),
                         ([110,100,900,700],'left'),([100,110,700,900],'top')]:
            result={'pixels':box,'status':'review','edges':{
                k:{'status':'snapped','score':1} for k in ('left','top','right','bottom')}}
            result['edges'][weak]={'status':'unresolved'}
            adjusted=refine_ratio(result,(1200,1200))
            self.assertEqual(adjusted['pixels'],box)
            self.assertEqual(adjusted['ratio_adjustment']['reason'],'expansion_skipped')

    def test_postprocess_preserves_detected_boundary_when_ratio_far(self):
        im=Image.new('RGB',(1000,1600),'white')
        ImageDraw.Draw(im).rectangle((120,140,879,1439),fill='#502910')
        result=postprocess_box(im,[115,85,885,905])
        self.assertEqual(result['pixels'],[120,140,880,1440])
        self.assertEqual(result['ratio_adjustment']['reason'],'ratio_too_far')

    def test_ratio_only_breaks_tie_between_observed_edges(self):
        pools=[[{'position':0,'score':1}], [{'position':0,'score':1}],
               [{'position':600,'score':1}], [{'position':800,'score':1},{'position':820,'score':1}]]
        self.assertEqual(ratio_tiebreak(pools,[0,0,600,810],[20]*4)['pixels'],[0,0,600,800])
        # A perfect ratio cannot override substantially better boundary evidence.
        pools[3][0]['score']=.8
        self.assertIsNone(ratio_tiebreak(pools,[0,0,600,810],[20]*4))

    def test_ratio_does_not_invent_missing_edge_or_break_near_tie(self):
        pools=[[{'position':0,'score':1}], [{'position':0,'score':1}],
               [{'position':600,'score':1}], []]
        self.assertIsNone(ratio_tiebreak(pools,[0,0,600,810],[20]*4))
        pools[3]=[{'position':799,'score':1},{'position':801,'score':1}]
        self.assertIsNone(ratio_tiebreak(pools,[0,0,600,800],[20]*4))

    def test_rectangle_edges_and_internal_photo_not_selected(self):
        im=Image.new('RGB',(1000,1600),'white')
        draw=ImageDraw.Draw(im)
        draw.rectangle((120,140,879,1439),fill='#502910')
        draw.rectangle((135,160,864,1419),fill='black')
        result=snap_box(im,[115,85,885,905])
        self.assertEqual(result['pixels'],[120,140,880,1440])
        self.assertEqual(result['status'],'resolved')

    def test_invisible_white_boundary_not_guessed(self):
        im=Image.new('RGB',(1000,1600),'white')
        result=snap_box(im,[100,100,900,900])
        self.assertEqual(result['pixels'],result['coarse_pixels'])
        self.assertEqual(result['status'],'review')

    def test_edges_outside_search_window_not_used(self):
        im=Image.new('RGB',(1000,1600),'white')
        ImageDraw.Draw(im).rectangle((200,300,799,1299),fill='brown')
        result=snap_box(im,[100,100,900,900])
        self.assertEqual(result['pixels'],result['coarse_pixels'])
        self.assertEqual(result['status'],'review')


if __name__=='__main__':unittest.main()
