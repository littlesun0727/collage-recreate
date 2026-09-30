# 按需使用的本地绘制参数

全部是可选style字段；颜色为#RRGGBB或#RRGGBBAA。长度（字体、描边、照片圆角、相纸padding、阴影、虚线）均为原图像素，程序统一缩放。shape的radius仍为相对短边比例0..1，与照片corner_radius不同。

| 用途 | 参数与默认 |
|---|---|
| 共用 | fill、stroke、stroke_width、opacity(0..1)、shadow |
| 文字 | font=sans/serif/mono/hand/cjk/cjk-hand，bold、italic、font_size（省略自动适配）、align=left/center/right、line_spacing |
| 照片 | corner_radius（像素）、feather(0..0.5)、outline_width、outline_color；outline作用最终外轮廓，普通照片同样生效 |
| 历史一体相纸兼容 | card={fill,padding:[上,右,下,左],radius}；旧JSON的cover照片可读，bbox包括整个相纸。新分析不使用此写法，改为独立载体overlay与照片窗口 |
| 阴影 | shadow={}使用offset=[3,5]、blur=8、color=#00000040；可覆盖任意字段，偏移方向相对画布 |
| 形状 | shape=rectangle/rounded_rectangle/ellipse/heart/star/line/arrow/polygon/polyline/curve/paper/tape/frame/paperclip/rays |
| 形状细节 | points（局部0..1坐标），radius，sides(3..30)，inner_ratio(0.05..0.95) |
| 线条 | dash=[线段长,间隔长]、line_cap=butt/round；曲线使用shape=curve及沿路径排列的少量points，程序平滑插值，不能用round端帽代替曲线 |
| 纸与纹理 | edge=straight/torn（paper/tape），默认整齐直边；texture=none/paper/grain/dots/stripes/grid，seed可省略 |
| 提取 | extract_mode=auto/foreground/color/crop；指定extract_background时auto去底色，否则前景分离；extract_tolerance默认35 |

简单圆角照片只填顶层`appearance:"rounded_photo"`，默认半径为短边8%。拍立得统一用photo内口加独立相纸overlay，见analysis.md；`appearance:"polaroid"`与card只兼容历史数据。需要差异时再加style，不逐个填写默认值。frame是透明中空均匀轮廓，不能代替下沿较宽的相纸；简单相纸可用paper/rectangle外形加已有photo_id或photo.parent_id窗口留孔；复杂轮廓仍用extract。

```json
{"shape":"curve","points":[[0.1,0.1],[0.6,0.3],[0.8,0.7],[0.2,0.9]],"stroke":"#FFFFFF","stroke_width":2,"dash":[6,5],"line_cap":"round"}
```

points只在line/arrow/polyline/curve/polygon中使用，polygon至少3点。自由曲线的关键走向需要points，默认工具不能从description猜出任意路径。frame可使用dash，内部保持透明。

字体是本机候选（Arial/Times/Courier/Comic/微软雅黑/楷体）的近似。纸纹和规则图形也不保证还原特殊手绘或摄影质感。提取后的候选必须看实际像素；crop仅用于确认不含参考照片的矩形素材，程序对与photo框重叠的原样裁图仍保守拒绝。不要以crop绕过人物替换。

## 提取不可用时的本地兜底

已有明确shape时可自动回退；没有参数时，一次screen的draw指定现有shape和样式，舍弃提取像素直接重画。关联cover照片按原bbox、rotation与圆角留透明孔，可保留不同边宽；不挖原提取层，不改变客户照片。简单相纸、规则纸底、胶带与几何装饰适合此路径。复杂插画、印字或独特纹理不能由几何兜底冒充，仍报告缺口。详见[筛选与补画](asset-gate.md)。
