"""照片窗口的七种轮廓。每个形状函数返回局部像素点，原点在制作框左上角。

按顺序连线并闭合即可得到裁剪路径。window_outline 接受场景层字段，
负责最后的平移和旋转，返回可直接用于画布的点。
"""
import math

import numpy as np

from .window_contract import INNER_RATIO_DEFAULT, RADIUS_DEFAULT, STAR_POINTS_DEFAULT


def rectangle_outline(width, height):
    """矩形。输入框宽、高（像素）；输出从左上角顺时针排列的四个局部顶点。"""
    return [(0, 0), (width, 0), (width, height), (0, height)]


def rounded_rect_outline(width, height, radius=RADIUS_DEFAULT):
    """圆角矩形。输入框宽高及圆角占短边的比例；输出包含四段圆弧的局部轮廓。"""
    corners = rectangle_outline(width, height)
    return rounded_quad_outline(corners, [radius] * 4)


def ellipse_outline(width, height):
    """椭圆。输入外接框宽、高（像素）；输出从最右端开始的一圈局部采样点。"""
    angles = np.linspace(0, 2 * math.pi, 360, endpoint=False)
    x = width * (0.5 + 0.5 * np.cos(angles))
    y = height * (0.5 + 0.5 * np.sin(angles))
    return list(zip(x, y))


def circle_outline(width, height):
    """内接圆。输入可用框宽、高（像素）；输出以框中心为圆心的局部轮廓。"""
    diameter = min(width, height)
    left = (width - diameter) / 2
    top = (height - diameter) / 2
    return [(left + x, top + y) for x, y in ellipse_outline(diameter, diameter)]


def star_outline(width, height, star_points=STAR_POINTS_DEFAULT, inner_ratio=INNER_RATIO_DEFAULT):
    """星形。输入框宽高、外尖数量和内外半径比；输出从正上方外尖开始的交替顶点。"""
    points = []
    for index in range(2 * star_points):
        angle = index * math.pi / star_points
        radius = 0.5 if index % 2 == 0 else 0.5 * inner_ratio
        x = width * (0.5 + radius * math.sin(angle))
        y = height * (0.5 - radius * math.cos(angle))
        points.append((x, y))
    return points


def heart_outline(width, height, steps=80):
    """心形。输入框宽高和每段曲线的采样数；输出从凹口起顺时针排列的 240 个局部点。"""
    # 从凹口沿右边走到尖端。每段依次给出起点、两个控制点、终点。
    segments = [
        ((.5, .18), (.535, .18), (.57, 0.), (.76, 0.)),
        ((.76, 0.), (.91, 0.), (1., .11), (1., .28)),
        ((1., .28), (1., .56), (.68, .84), (.525, .98)),
        ((.525, .98), (.515, .989), (.507, 1.), (.5, 1.)),
    ]
    t = np.linspace(0, 1, steps, endpoint=False)[:, None]
    curves = []
    for start, control1, control2, end in segments:
        curve = ((1-t)**3 * np.array(start)
                 + 3 * (1-t)**2 * t * np.array(control1)
                 + 3 * (1-t) * t**2 * np.array(control2)
                 + t**3 * np.array(end))
        curves.append(curve)
    right = np.vstack([*curves, [[.5, 1.]]])

    # 沿弧长均匀取点，再镜像得到左半心。凹口和尖端各保留一次。
    distances = np.linalg.norm(np.diff(right, axis=0), axis=1)
    arc_length = np.r_[0, np.cumsum(distances)]
    marks = np.linspace(0, arc_length[-1], 121)
    right = np.column_stack([np.interp(marks, arc_length, axis) for axis in right.T])
    left = right[-1:0:-1].copy()
    left[:, 0] = 1 - left[:, 0]
    points = np.vstack([right[:-1], left])
    unit = (points - points.min(axis=0)) / np.ptp(points, axis=0)
    return [tuple(point) for point in unit * [width, height]]


def polygon_outline(width, height, points, corner_radii=None):
    """多边形。输入框宽高、依次排列的 0～1 顶点；输出局部像素轮廓。
    四边形可另传四个圆角比例，与顶点一一对应，0 表示保留尖角。
    """
    vertices = [(u * width, v * height) for u, v in points]
    if corner_radii is not None:
        return rounded_quad_outline(vertices, corner_radii)
    return vertices


def rounded_quad_outline(points, radii):
    """给四边形倒圆角。输入四个像素顶点及各角半径比例，输出同一坐标系的轮廓点。"""
    vertices = np.asarray(points, dtype=float)
    edges = np.roll(vertices, -1, axis=0) - vertices
    lengths = np.linalg.norm(edges, axis=1)
    if min(lengths) < 1e-6:
        return vertices.tolist()

    # 每个顶点沿相邻两条边各走一段切线距离，得到圆弧的起点和终点。
    outgoing = edges / lengths[:, None]
    incoming = -np.roll(outgoing, 1, axis=0)
    cosine = np.sum(incoming * outgoing, axis=1)
    half_angle = np.arccos(np.clip(cosine, -1, 1)) / 2
    short_side = min((lengths[0] + lengths[2]) / 2, (lengths[1] + lengths[3]) / 2)
    radii_px = short_side * np.asarray(radii)
    tangent = radii_px / np.maximum(np.tan(half_angle), 1e-8)

    # 若相邻两角挤在同一条边上，等比缩小所有圆弧，避免切点交叉。
    occupied = tangent + np.roll(tangent, -1)
    scale = min(1., float(np.min(lengths / np.maximum(occupied, 1e-8))))
    outline = []
    for i, vertex in enumerate(vertices):
        if radii_px[i] <= 0:
            outline.append(tuple(vertex))
            continue
        distance = tangent[i] * scale
        bisector = incoming[i] + outgoing[i]
        center = vertex + bisector * distance / (2 * max(math.cos(half_angle[i])**2, 1e-12))
        start = vertex + incoming[i] * distance - center
        end = vertex + outgoing[i] * distance - center
        cross = start[0] * end[1] - start[1] * end[0]
        sweep = math.atan2(cross, np.dot(start, end))
        start_angle = math.atan2(start[1], start[0])

        # 按像素半径选采样密度，将弧与折线之间的误差控制在约 0.2 像素内。
        radius = radii_px[i] * scale
        max_step = min(math.pi / 8, 2 * math.acos(1 - .2 / max(radius, .2)))
        count = max(2, math.ceil(abs(sweep) / max_step))
        angles = np.linspace(start_angle, start_angle + sweep, count + 1)
        outline.extend(zip(center[0] + radius * np.cos(angles), center[1] + radius * np.sin(angles)))
    return outline


def unit_outline(kind, star_points=STAR_POINTS_DEFAULT, inner_ratio=INNER_RATIO_DEFAULT):
    """轴向拟合入口。输入心形、星形或椭圆参数，输出单位框中的轮廓点。"""
    if kind == 'heart':
        return heart_outline(1, 1)
    if kind == 'star':
        return star_outline(1, 1, star_points, inner_ratio)
    if kind == 'ellipse':
        return ellipse_outline(1, 1)
    raise ValueError('No unit outline for shape ' + str(kind))


def window_outline(fields):
    """场景入口。输入照片层（bbox、rotation 和形状参数），输出画布像素点数组。
    cutout 的外形由照片主体决定，返回 None。bare 在布局计算中使用矩形占位。
    """
    if fields.get('mode') == 'cutout':
        return None
    left, top, right, bottom = fields['bbox']
    width, height = right - left, bottom - top
    shape = fields.get('shape', 'rectangle')
    if shape == 'rectangle':
        local = rectangle_outline(width, height)
    elif shape == 'rounded_rect':
        local = rounded_rect_outline(width, height, fields.get('radius', RADIUS_DEFAULT))
    elif shape == 'circle':
        local = circle_outline(width, height)
    elif shape == 'ellipse':
        local = ellipse_outline(width, height)
    elif shape == 'heart':
        local = heart_outline(width, height)
    elif shape == 'star':
        local = star_outline(width, height, fields.get('star_points', STAR_POINTS_DEFAULT),
                             fields.get('inner_ratio', INNER_RATIO_DEFAULT))
    elif shape == 'polygon':
        local = polygon_outline(width, height, fields['points'], fields.get('corner_radii'))
    else:
        raise ValueError('No outline for shape ' + str(shape))

    # 形状函数只关心局部轮廓。所有形状共用一次绕框中心的顺时针旋转与画布定位。
    points = np.asarray(local, dtype=float)
    angle = math.radians(fields.get('rotation', 0))
    if angle:
        center = np.array([width / 2, height / 2])
        cosine, sine = math.cos(angle), math.sin(angle)
        rotation = np.array([[cosine, -sine], [sine, cosine]])
        points = (points - center) @ rotation.T + center
    return (points + [left, top]).astype(np.float32)
