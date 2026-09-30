# 联合分析与绑定

集中看参考图和客户联系表，输出两个严格UTF-8 JSON：analysis描述设计及内容归属，bindings记录客户素材选择。无注释、代码围栏、重复键或NaN。先确定完整设计单元，再选制作方式；常见外观用默认值，不为减少对象而丢掉照片槽或必要文案。

## 原尺寸与坐标

参考与定位图读取、转发都保留original：

```javascript
const picture = await tools.view_image({path: "RUN/prepared/reference.png", detail: "original"});
image(picture.image_url, "original");
```

reference_size使用prepare返回的实际尺寸。bbox为原图像素 `[左,上,右,下]`，右下可等于图宽高；不使用查看器缩略尺寸。rotation顺时针为正，默认0；bbox是旋转前制作框，以其中心旋转。layer_order从底到顶，每个ID出现一次。parent_id只记录关联，不自动联动位置。

正常估计对象原始bbox；不要为了提取手动外扩。build会在请求360时自动每边外扩10%，原始布局框和照片窗口独立保留。

## 先判归属，再确定完整单元

1. **客户照片内容随照片替换。** 每个独立显示、需要替换的位置是一张photo。人物手持物、服饰、首饰、照片中的招牌和环境默认属于同一照片；抠图主体也可能包含其手持物，不等于只取人体。不能因物体显眼、写实或可分割就列为固定overlay。只有明确的独立贴纸/排版证据或用户要求才分离。不确定时在photo的description说明，不默认把参考物品搬到客户身上。客户素材没有同款物品，不要求补出参考物品。
2. **固定装饰默认完整保留。** 一个纸张连同纹样、一个线条组合连同分支端点、一个装订结构连同连接部件，优先各为一个overlay。相邻贴纸能共同移动、缩放且与客户照片共用同一前后关系时，也可作为完整组合；例如固定叠贴的猫头、星星、可颂与箭头。照片后方的纸底不能仅因靠近贴纸就合入照片前方组合。内部横线、圆点、描边、星芒短线、附属小图案只写进description，不逐笔建立ID。分开必须有独立编辑/替换或无法共用一个前后层级的理由；不要为适配本地shape拆零件。不设对象数量上限，也不把空间很远、归属不同的同色图案强行放进巨大框。
3. **照片窗口与固定载体统一分开。** 普通白边拍立得、装饰相框、相册底板都遵循同一表达：photo的bbox只表示可替换照片内口；相纸、边框及附着装饰合为完整overlay，其bbox覆盖载体全幅。单窗overlay用photo_id关联照片；多窗载体中的各photo用parent_id指向同一overlay，保留各自bbox和绑定。不要按相框简单/复杂或本地绘制/提取选择不同拆分，也不要把框的四条边另拆对象。照片自身的圆角、细描边仍是photo属性，不为它们新增相纸对象。返回已透明时直接回填；确认旧照片残留才按需清窗。现有工具支持逐个指定规则多窗口，复杂镂空仍需登记限制，不把旧人物当装饰保留。
4. **文字只制作一次。** 普通标题、正文、需替换文案保留text，同样式多行合为一项。固定贴纸内的特殊字标、不可分的印刷纹样可随owner整体保留，在description准确记录可辨文字。普通文字即使印在纸上仍可独立保留；若当前随固定载体提取且不改字，用embedded_in明确归属，避免提取原字又叠一遍。需改字时保持独立，在载体description注明原字需清理，不能把关联当作已完成去字。不可辨文字不编造原文。

每个组合的description只需短写关键成员、排列/连接关系和透明间隙，不再输出一份子元素制作清单。主体内容与设计贴纸的区分依据照片连续性、独立轮廓和排版关系；同为杯子，人物手里的杯子可以属于照片，独立杯子贴纸则属于装饰。

## 完整范围与制作建议

- bbox包住该单元全部可见成员，包括细线末端、附属图案、描边与阴影；照片槽描述完整显示窗口，不按遮挡后剩余碎片缩框。描述里列了的成员应在同一bbox内。不可确定的隐藏部分不编造，画布外部分截至边界。
- 圆角、描边、阴影通常是所属对象的属性；相纸及其阴影归载体overlay。新分析不使用photo的`appearance:"polaroid"`或`style.card`派生相纸，避免照片bbox同时表示外框和内口。frame形状仅表示均匀轮廓线，不能用它冒充下沿较宽的拍立得相纸。
- 完整单元确定后才选method：单个可准确表达的简单几何用local；带手绘差异、纹样、多个关联成员的固定装饰优先extract。不要把材质丰富的底板简化成纯色块后声称已表达完整，也不要为使用local把组合拆成基本形状。description不会自动变成绘图或提取指令；extract只是制作建议，不保证服务保留全部成员。
- 写完在同一次分析内快速检查：照片自然内容是否被误列装饰；重复小零件是否属于一个整体；连接部分是否漏记；文字是否重复归属；各客户窗口和真实遮挡是否仍完整。无需额外模型调用或逐对象验收表。

## analysis.json

每个对象必需id/kind/bbox/label/description。id以小写字母开头，可含小写字母、数字、下划线、短横线，最长64。kind为background/photo/text/overlay。

- photo：mode默认cover；柔边用feather，人物前景用cutout。圆角照片可选appearance为rounded_photo；其他照片省略。style按需填写，固定相纸另列overlay。
- text：text与text_status必需；已知为非空文字/known，不可辨为null/unreadable。
- overlay：method为local/extract/placeholder，默认local；local必须提供支持的shape。复杂材质缺口用placeholder并说明，不用明显错误的几何冒充已经还原。
- background：简单底色可只写style.fill。需要替换的场景照片用photo绑定客户素材。

沿用已有关系字段，不增加新的必填字段：

- 单窗相框overlay填写 `photo_id: "photo_main"` 指定其照片，制作端用photo的bbox定位窗口，确需清窗时也沿用此范围；不依赖模型省略关联后由程序猜测。
- 新分析photo的bbox本身就是窗口，省略window_bbox。历史JSON中的window_bbox、一体polaroid与style.card仍由程序兼容读取，但不作为新分析的另一种写法。
- 明确随纸片整体保留的字/装饰，可用 `embedded_in: "paper"` 指向载体overlay。普通可编辑文字保持独立。不要只因bbox相交就填写；现有parent_id明确归属于提取层时也可复用。程序还会检查该位置确有图像内容。未明确归属的疑似重复文字会保留并在整图复核中提示。

```json
{
  "schema_version":"collage-analysis-v1",
  "reference_size":[800,1200],
  "objects":[
    {"id":"background","kind":"background","bbox":[0,0,800,1200],"label":"底色","description":"米白底","style":{"fill":"#EEE8DD"}},
    {"id":"photo_main","kind":"photo","bbox":[110,180,690,870],"label":"主相片窗口","description":"客户照片内口，不含相纸"},
    {"id":"photo_paper","kind":"overlay","bbox":[80,150,720,1000],"label":"完整相纸","description":"暖白拍立得相纸，下沿较宽；清除内口原照片，保留完整纸边","method":"extract","photo_id":"photo_main"},
    {"id":"title","kind":"text","bbox":[100,40,700,110],"label":"标题","description":"黑色粗衬线","text":"together.","text_status":"known","style":{"font":"serif","bold":true,"fill":"#171717"}},
    {"id":"heart","kind":"overlay","bbox":[610,100,740,240],"label":"爱心","description":"粉色爱心","style":{"shape":"heart","fill":"#EFC8D7"}}
  ],
  "layer_order":["background","photo_main","photo_paper","title","heart"]
}
```

800×1200仅为示例。所有style字段可选；使用时必须来自 [本地绘制参数](drawing.md)，不发明字段。默认圆角用rounded_photo，无需估算半径；相框先按固定载体描述，再决定制作方式。shape、曲线路径等只在对应元素需要时填写。

## bindings.json

读取prepared/catalog.json的实际asset ID，查看联系表后选择素材。每个photo恰好一个绑定，除非素材数不够，否则不可复用同一素材；源文件由程序核验。

```json
{
  "schema_version":"collage-bindings-v1",
  "photos":[{"slot_id":"photo_main","asset_id":"asset_012345abcdef","reason":"景别适合主槽"}],
  "texts":[]
}
```

示例asset ID必须替换为catalog的真实值。crop_center默认[0.5,0.5]，范围0..1；可选source_crop=[0,0,1,1]、mirror_x=true。cutout的source_crop相对于已抠出主体的透明边界。

不可辨文字可在texts中补`{"object_id":"caption","text":"新创作短句","origin":"generated","reason":"替代不可辨内容"}`；origin为user/generated，不能冒充识别原文或猜事实姓名日期。已知文字不由bindings覆盖。

正常制作中两个JSON写好后直接build，程序会校验并产出叠框；出现定位问题再查看叠框排错。用户只要求解析时，到JSON与解析可视化为止，不运行build、提取、抠图、生成或成品复核。
