"""Gated synthetic renderer for browser progress tests; never invokes remote services."""
import sys
from pathlib import Path

from test_workbench import run as fixture, build, adjust, review, save, checkpoint
import render as renderer


root=Path(sys.argv[1]);root.mkdir(parents=True,exist_ok=True)
task=fixture.__wrapped__(root)
photo=renderer.make_photo;overlay=renderer.draw_overlay


def gate(phase):
    save(root/'phase.json',{'phase':phase})
    if sys.stdin.readline().strip()!='continue':
        raise RuntimeError('Fixture stopped')


def gated_photo(*args,**kwargs):
    gate('photo')
    return photo(*args,**kwargs)


def gated_overlay(obj,*args,**kwargs):
    if obj['id']=='star':gate('star')
    return overlay(obj,*args,**kwargs)


renderer.make_photo=gated_photo;renderer.draw_overlay=gated_overlay
build(task)
renderer.make_photo=photo;renderer.draw_overlay=overlay
gate('first')
adjust(task);review(task);checkpoint(task,6,'complete','合成测试任务已完成，不是真实客户样片')
save(root/'phase.json',{'phase':'finished'})
