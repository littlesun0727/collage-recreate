from collage.crop_quality import expand_crop


def pixels(crop, size):
    l,t,r,b = crop
    w,h = size
    return int(r*w)-int(l*w), int(b*h)-int(t*h)


def test_75_percent_is_sufficient_without_change():
    crop = [0,0,.6,.3]
    assert expand_crop(crop, (1000,1000), (800,400)) == crop


def test_small_crop_expands_at_boundary_and_meets_both_axes():
    crop = expand_crop([.9,.9,1,1], (1000,1000), (800,400))
    w,h = pixels(crop, (1000,1000))
    assert w >= 600 and h >= 300
    assert crop[2:] == [1,1]
    assert all(0 <= v <= 1 for v in crop)


def test_cover_uses_effective_pixels_after_aspect_crop():
    crop = expand_crop([0,0,1,.1], (2000,1000), (800,400))
    w,h = pixels(crop, (2000,1000))
    effective_w, effective_h = min(w,h*2), min(h,w/2)
    assert effective_w >= 600 and effective_h >= 300


def test_insufficient_original_uses_full_frame_without_rejection():
    assert expand_crop([.4,.4,.5,.5], (200,100), (800,400)) == [0,0,1,1]


def test_repair_is_idempotent():
    fixed = expand_crop([.82,.83,.93,.97], (1308,736), (900,500))
    assert expand_crop(fixed, (1308,736), (900,500)) == fixed
