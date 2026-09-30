"""Summarize the frozen production-build experiment without inventing visual scores."""
import argparse
import statistics
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import read,save
p=argparse.ArgumentParser();p.add_argument('--root',required=True);args=p.parse_args();root=Path(args.root);s=read(root/'summary.json');v=read(root/'verification.json')
durations=[c['review_seconds'] for c in s['cases_detail'] if c.get('review_state')=='completed']
photo_proofs=v['photo_slots'];states=s['statuses']
report=['# v5 提取回填：固定输入的制作路径实测','',
f"{s['cases']}张首版，指定模型完成{v['completed_sdk_reviews']}张复核。首版通过{states.get('verified',0)}张，需要修改{states.get('needs_changes',0)}张。不是端到端重新分析实验，也不是人工质量真值。",'',
'已经接入：严格JSON → 单次build内提取/清窗/客户照片回填 → 集中视觉复核。没有新增规划代理或逐素材模型验收。API可提交、续查、下载；这批全部复用已付费缓存，没有新提取或生图请求。','',
f"素材处理：{s['actions'].get('reuse_layer',0)}层直接复用，{s['actions'].get('clear_photo_window',0)}个照片窗口清理，{s['actions'].get('local_fallback',0)}个目标回退。{photo_proofs}个客户照片槽来源核验通过，清窗mask内原图alpha均为0；这些机械检查不代表视觉污染、错裁切或叠字不存在。",'',
f"缓存build中位数{s['offline_build_median_seconds']}秒，范围{s['offline_build_range_seconds']}秒。历史提取服务17个任务中位数56.453秒，不含上传/下载/轮询；这两项都不能冒充完整制作耗时。",'']
if durations:report += [f"本次独立SDK视觉复核中位数{statistics.median(durations):.2f}秒，范围{min(durations):.2f}–{max(durations):.2f}秒，包括读文件、看图、工具调用、写review及终端重试；不是纯模型推理时间。",'']
report += ['实际限制：复杂织物/多人底板/不规则窗口未通用解决；旧分析未写明的嵌入文字会保留并标为重复候选；客户素材替换后的文字对比度、人物裁切仍可能出问题。首次集成还发现稀疏大路径误回退，后续修正需单列，不改本批冻结首版。','',
'| 样本 | build秒 | 模型复核秒 | 结论 | 主要问题 |','|---|---:|---:|---|---|']
for c in s['cases_detail']:
    issues='；'.join(str(i).replace('|','/').replace('\n',' ') for i in c['visual_issues'])
    report.append(f"| {c['case']} | {c['build_seconds']} | {c.get('review_seconds') or '-'} | {c['status']} | {issues or '-'} |")
report += ['', '检查与证据：verification.json核对原始analysis/bindings哈希、资源来源、照片可见性、19个清窗、冻结首版、真实SDK会话的模型/effort、原尺寸对照图载荷及每图一次会话。html-check.json记录链接和PNG/JPEG解码检查。review-controller保留原始模型结论。','',
'自动测试与API边界：初始集成43项回归通过；自动远程请求仅做HTTP替身测试，本批没有付费在线冒烟。新增回归捕获的后续修复单独记录，不把文件存在当作质量通过。','',
'[查看HTML](index.html) · [复现方法](REPRODUCE.md) · [汇总](summary.json) · [核验证据](verification.json)','']
(root/'REPORT.md').write_text('\n'.join(report),encoding='utf-8')
save(root/'review-metrics.json',{'completed':len(durations),'median_seconds':round(statistics.median(durations),3) if durations else None,'range_seconds':[min(durations),max(durations)] if durations else []})
print('REPORT.md written',states)
