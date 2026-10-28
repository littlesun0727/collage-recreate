"""Visualize saved V9 task outputs without rerunning or altering the task."""
from html import escape
import base64
import json
from pathlib import Path
from PIL import Image


def overview(data):
    assets = Path(__file__).resolve().parent / 'assets/task-49d00684cd771938'
    task = json.loads((assets/'task.json').read_text(encoding='utf-8'))
    structure = json.loads((assets/'structure.json').read_text(encoding='utf-8'))
    windows = json.loads((assets/'photo-windows.json').read_text(encoding='utf-8'))
    bindings = json.loads((assets/'bindings.json').read_text(encoding='utf-8'))
    objects = {o['id']:o for o in task['reference_objects']}
    binding_by_id = {o['slot_id']:o for o in bindings['photos']}
    window_by_id = {o['photo_id']:o for o in windows['photos']}
    data['overview_evidence'] = dict(task_id=task['id'], version=task['versions'][-1]['id'],
        render_id=task['versions'][-1]['render_id'], structure=structure, windows=windows,
        bindings=bindings, sources=json.loads((assets/'image-sources.json').read_text(encoding='utf-8')))
    def asset(name):
        return 'data:image/png;base64,' + base64.b64encode((assets / name).read_bytes()).decode('ascii')
    ref = asset('reference.png')
    red, ink, muted = '#b00000', '#222222', '#666666'
    p = ['<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="1320" viewBox="0 0 1800 1320" role="img" aria-label="三个 VLM 的实际产物可视化">',
         '<style>text{font-family:"Microsoft YaHei","Segoe UI",sans-serif;fill:#222}.node{cursor:pointer}.node:hover .card,.node:focus .card{stroke:#b00000;stroke-width:2.5}</style>',
         '<defs><marker id="arrow" markerWidth="9" markerHeight="9" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" fill="none" stroke="#999" stroke-width="1.5"/></marker></defs>']

    def rect(x,y,w,h,fill='#fff',stroke='#d6d6d6',dash=False,extra=''):
        p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="{stroke}" {extra}'+(' stroke-dasharray="6 5"' if dash else '')+'/>')

    def text(x,y,s,size=18,color=ink,bold=False):
        p.append(f'<text x="{x}" y="{y}" font-size="{size}" style="fill:{color}" font-weight="{700 if bold else 400}">{escape(str(s))}</text>')

    def line(d,dash=False):
        p.append(f'<path d="{d}" fill="none" stroke="#999" stroke-width="2" '+('stroke-dasharray="6 5"' if dash else 'marker-end="url(#arrow)"')+'/>')

    def img(url,x,y,w,h):
        p.append(f'<image href="{url}" x="{x}" y="{y}" width="{w}" height="{h}" preserveAspectRatio="xMidYMid meet"/>')

    def crop(url,bbox,x,y,w,h,source_size=(1080,1477)):
        # Vector viewport only: embedded source image bytes remain unchanged.
        a,b,c,d=bbox
        scale=min(w/(c-a),h/(d-b))
        cw,ch=(c-a)*scale,(d-b)*scale
        p.append(f'<svg x="{x+(w-cw)/2}" y="{y+(h-ch)/2}" width="{cw}" height="{ch}" viewBox="{a} {b} {c-a} {d-b}" overflow="hidden"><image href="{url}" width="{source_size[0]}" height="{source_size[1]}"/></svg>')

    def extracted(name,x,y,w,h):
        with Image.open(assets/name) as im:
            bounds=im.getchannel('A').getbbox() if im.mode=='RGBA' else (0,0,*im.size)
            crop(asset(name),bounds,x,y,w,h,im.size)

    rect(0,0,1800,1320,'#fff','#fff')
    text(48,54,'COLLAGE / V9',18,red,True)
    text(48,104,'三次视觉判断，把参考设计转换为客户照片拼贴',36,bold=True)
    text(48,145,'参考图决定结构和显示方式 · 客户素材决定照片内容 · 程序执行提取与合成',20,muted)
    rect(48,190,260,850,'#f5f5f5')
    text(72,235,'输入与准备',24,bold=True)
    img(ref,85,285,186,247)
    text(91,564,'参考设计 · 拼贴 6',16,muted)
    img(asset('material_00.png'),72,640,105,150)
    img(asset('material_09.png'),184,640,105,150)
    text(73,867,'客户素材 / 视频封面',18,bold=True)
    text(73,899,'说明 + 编号 + 联系表',17,muted)
    rect(346,190,1090,790,'#fafafa')
    text(374,235,'VLM 判断层',23,bold=True)
    text(1127,235,'默认路径：3 次请求',18,red,True)
    specs=[('analyze',374,'01  ANALYSIS','理解参考设计','objects + layer_order','对象框、类别与层序'),
           ('photo_windows',723,'02  PHOTO-WINDOW','确定照片显示方式','photos + placement','窗口、主体与边缘参数'),
           ('bind',1072,'03  BINDING','客户选材与裁切','photos + asset_id','槽位与客户素材的对应关系')]
    for stage,x,label,title,out,caption in specs:
        p.append(f'<g class="node" data-stage="{stage}" role="button" tabindex="0" aria-label="展开{title}">')
        rect(x,285,308,625,extra='class="card"')
        p.append(f'<path d="M{x+12} 285 H{x+296}" stroke="#c7000b" stroke-width="3"/>')
        text(x+20,322,label,16,red,True)
        text(x+20,363,title,23,bold=True)
        text(x+20,397,caption,16,muted)
        if stage=='analyze':
            img(asset('boxes.png'),x+30,420,248,331)
            text(x+20,779,'实际叠框：照片 / 装饰 / 文字',15,muted)
            text(x+20,808,f"{len(structure['objects'])} 个对象 · {len(windows['photos'])} 个照片槽",16)
            text(x+20,835,'structure.json：对象清单 + 层序',14,muted)
        elif stage=='photo_windows':
            crop(asset('windows.png'),objects['top_photo']['bbox'],x+20,430,126,120)
            text(x+158,452,'top_photo',16,red,True)
            text(x+158,486,'矩形内口',16,bold=True)
            text(x+158,516,'保留摄影背景',14,muted)
            text(x+158,546,'无描边 / 渐变',14,muted)
            rect(x+20,588,126,171,'#eee','#eee')
            extracted('large_portrait.png',x+24,592,118,163)
            text(x+158,615,'large_portrait',14,red,True)
            text(x+158,650,'去背 + 白色外描边',13,bold=True)
            stroke=window_by_id['large_portrait']['subject']['stroke']['width']
            text(x+158,680,f'宽度：{stroke*100:g}% 短边',14,muted)
            text(x+158,710,'无固定窗口',14,muted)
            text(x+158,740,'下图为实际执行效果',12,muted)
            text(x+20,805,'上：实际窗口预览 · 下：客户分割层',14,muted)
            text(x+20,832,'参数来自 photo-windows.json',14,muted)
        else:
            text(x+20,433,'参考槽位',14,muted)
            text(x+175,433,'实际客户素材',14,muted)
            for i,slot in enumerate(['top_photo','middle_photo','large_portrait']):
                item=binding_by_id[slot];name=item['asset_id']+'.png'; yy=448+i*119
                crop(ref,objects[slot]['bbox'],x+22,yy,98,83)
                img(asset(name),x+190,yy,62.25,83)
                a,b,c,d=item['source_crop']
                p.append(f'<rect x="{x+190+a*62.25}" y="{yy+b*83}" width="{(c-a)*62.25}" height="{(d-b)*83}" fill="none" stroke="{red}" stroke-width="1.5"/>')
                line(f'M{x+130} {yy+40} H{x+178}')
                text(x+20,yy+104,slot,12,muted)
                text(x+180,yy+104,item['asset_id'],13,red)
            text(x+20,821,'红框为真实 source_crop · 节选 3 / 4',14,muted)
        rect(x+18,852,272,40,'#f5f5f5','#e3e3e3')
        text(x+29,879,out,17,red)
        p.append('</g>')
    text(396,947,'只看参考图',16,muted)
    text(744,947,'参考图 + photo/frame + 裁片',15,muted)
    text(1094,947,'参考图 + 联系表 + 客户单图',15,muted)
    line('M308 543 H374');line('M682 575 H723');line('M1031 575 H1072')
    line('M308 1010 H1226 V980');text(536,998,'客户素材与选材说明进入 Binding',18,muted)
    rect(1474,190,278,850,'#f5f5f5');text(1498,235,'本地执行与交付',23,bold=True)
    rect(1496,285,234,410);text(1514,322,'提取固定素材',22,bold=True)
    rect(1510,344,206,112,'#f5f5f5','#eee')
    extracted('lemon_sticker.png',1518,351,88,94)
    extracted('coffee_sticker.png',1613,351,94,94)
    text(1530,482,'柠檬贴纸',15,muted);text(1621,482,'咖啡贴纸',15,muted)
    rect(1510,501,206,112,'#f5f5f5','#eee')
    extracted('top_frame.png',1518,510,88,94)
    extracted('icecream_sticker.png',1613,510,94,94)
    text(1530,636,'黄色相框',15,muted);text(1621,636,'冰淇淋',15,muted)
    text(1514,675,'当前提取资源 · 状态待复核',14,muted)
    line('M1380 575 H1496');line('M1612 695 V743')
    rect(1496,743,234,279);text(1514,778,'final.png + scene.json',17,red,True)
    img(asset('current_image.png'),1551,792,124,166)
    text(1514,987,'V9 当前结果 · 第 2 版',16,muted)
    text(1514,1010,'已生成 · 未做整图视觉复核',13,muted)
    rect(723,1065,308,60,'#f5f5f5','#ccc',True);text(743,1103,'范围校准 · 已关闭',18,'#999')
    line('M1051 575 V1045 H875 V1065',True)
    rect(1117,1065,308,60,'#f5f5f5','#ccc',True);text(1137,1103,'相框审核 · 已关闭',18,'#999')
    line('M1612 719 H1446 V1035 H1271 V1065',True)
    rect(48,1160,1704,100,'#f5f5f5')
    text(73,1200,'默认首版：Analysis → Photo-window → Binding',25,red,True)
    text(73,1236,'本任务记录 3 次 VLM 请求；右侧展示素材恢复后的当前构建版本。节点配置与调用路径保持不变。',18,muted)
    text(49,1297,'V9 实际任务 49d00684cd771938 · 结构 / 窗口 / 绑定 / 提取资源 / 当前成图均来自同一任务。',16,muted)
    text(1450,1297,'渲染 '+task['versions'][-1]['render_id'],13,muted)
    p.append('</svg>')
    return ''.join(p)
