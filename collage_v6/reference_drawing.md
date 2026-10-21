## 参考分析中可用的本地绘制参数

先按 design_policy.md 确定完整素材单元和制作方式，再填写参数；有同名 shape 不代表能还原特殊笔触、纹样或艺术字。以下是当前渲染器的能力清单，不是应在参考图中寻找或优先使用的元素清单。local 对象只能表达这些能力覆盖的特征；固定背景无法可靠绘制时可选择 reference_clean，不强制填一套近似参数。

extract 对象不附加一套几何重画方案。frame 可填 style.fill 和 style.radius，供提取缺失或确认含原照片时程序绘制简化替代框；未声明颜色时使用中性浅色。替代框按原外框与关联照片窗口生成，不能视为已还原原框的纹样和材质。

- 颜色：#RRGGBB 或 #RRGGBBAA。字体、线宽、阴影和虚线长度使用原图像素；points 为对象局部 0–1 坐标，radius 为短边比例。
- 共用：fill、stroke、stroke_width、opacity（0–1）、shadow。shadow 可填 offset、blur、color。
- 普通文字：font=sans/serif/mono/hand/cjk/cjk-hand，bold、italic、font_size、align=left/center/right、line_spacing。省略 font_size 时程序适配；同样式多行合为一个 text。
- 规则形状：shape=rectangle/rounded_rectangle/ellipse/heart/star/line/arrow/polygon/polyline/curve/paper/tape/frame/paperclip/rays；按实际需要填 radius、sides、inner_ratio。
- 线条：dash=[线段长,间隔长]、line_cap=butt/round；line/arrow/polyline/curve/polygon 使用局部 points，polygon 至少 3 点。自由曲线需要路径点，description 不会变成路径。
- 纸和纹理：edge=straight/torn，texture=none/paper/grain/dots/stripes/grid，seed 可省略。弱纸纹可近似，独特材质不能宣称已精确还原。

style.shape=frame 只是本地透明中空的均匀轮廓，与 kind=frame（需独立提取的装饰相框）不同，不能冒充下沿较宽的相纸；简单相纸用 overlay 的 paper/rectangle 并关联独立 photo 内口。照片几何窗口的单色轮廓直接用 photo_windows.stroke，不创建本地框体。固定文字与载体遵循文字归属规则。照片窗口参数由 photo_windows 输出，不使用上述 shape 或旧 photo.style 字段代替。

只有参考中存在周期性重复且能保留其主要特征时，才使用 style.patterns 数组，最多四层。每项填写 kind=lines/grid/dots/cherry/reference_tile、spacing=[横向周期,纵向周期]、phase=[横向位移,纵向位移]、angle（顺时针角度）、color、width（线宽）。spacing/phase/width/dash/motif_size 使用参考像素；angle 使用角度。坐标旋转围绕该对象中心，phase 在旋转前图案坐标中定义。
lines 是平行线，grid 是正交网格，dots 是规则点阵；angle 控制方向，dash=[线段长,间隔长] 控制线段重复。cherry 是现有的双樱桃图元近似，motif_size 控制大小，仅在参考实际匹配时使用，不能替代其他图案。是否叠加及叠加何种图案由参考中的重复关系决定，不预设组合。
reference_tile 复用参考像素后本地平铺，不是重新绘制复杂图案。仅从干净无遮挡的重复单元取样。sample_bbox 使用整张参考图的 0–1 归一化坐标（不随全局 bbox 坐标模式变化）；选取完整周期，spacing 表示输出周期，通常与取样尺寸一致，angle=0 表示保持取样方向。程序按 photo.bbox 拒绝相交取样；取样也不得混入独立文字或装饰。fallback_motif=grid/dots/cherry/none 只用于取样失败后的明确降级，仅选择与参考相符的替代图案，否则填 none 并说明缺失。没有干净单元且图元无法表达时，不编造替代纹样；非周期性设计不使用平铺。
