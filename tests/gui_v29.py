"""Real Qt interactions for selected risk callouts, faint fills and the current-row border."""
import json,os,sys,tempfile,time
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.qt_compat import Qt,QtCore,BINDING
from die_shift_studio.width_witness import pose_text
if BINDING=='PyQt6':from PyQt6.QtTest import QTest
else:from PyQt5.QtTest import QTest
tmp=tempfile.TemporaryDirectory();root=Path(tmp.name);app=W.QApplication([]);w=MainWindow(jobs_dir=root/'Jobs')
w.resize(1490,940);w.show();errors=[];w.error=lambda value:errors.append(str(value))
keys=getattr(Qt,'KeyboardModifier',Qt);left=getattr(Qt,'MouseButton',Qt).LeftButton
def settle():
    until=time.monotonic()+180
    while time.monotonic()<until:
        app.processEvents()
        if not any((w.width_yield.worker,w.width_yield.timer.isActive(),w.worker,w.preview_workers,w.preview_pending,w.motion_worker,w.filter_job,w.hardware_worker,
                    w.coverage_timer.isActive(),w._coverage_pending,w.viewport_timer.isActive(),w.transition.timer.isActive())):
            app.processEvents();break
        time.sleep(.004)
    else:raise AssertionError('UI did not settle')
    assert not errors,errors
    assert '未处理异常' not in w.runtime_view.toPlainText(),w.runtime_view.toPlainText()[-1500:]

def click(xy,mod=keys.NoModifier):
    x,y=w.ax.transData.transform(xy);ratio=w.canvas.device_pixel_ratio
    QTest.mouseClick(w.canvas,left,mod,QtCore.QPoint(round(x/ratio),round(w.canvas.height()-y/ratio)))
    settle()

def labels():
    return '\n'.join(a.get_text() for a in w.width_overlay.labels if a.get_visible())

name='示例O_运动网络三线路通道';q=json.loads((ROOT/'examples'/(name+'.json')).read_text())
q['gds_path']=str(ROOT/'examples'/q['gds_path']);q['parameters'].update(target_nsigma=0,per_die_enabled=True,die_bounds=[[35,0,.7],[0,10,0]])
q.setdefault('settings',{}).update(animation_enabled=False)
q['overlays']={'width':True};src=root/'source.dshift.json';src.write_text(json.dumps(q));w.load_project(src);settle()
w.run_evaluation();settle();assert w.width_toggle.isChecked() and w.extreme_table.currentRow()==0
summary=w.current_frame['width_summary'];refs=sorted(summary['lines'])
assert len(refs)==3 and not labels().strip()
w.set_view((0,0),380,170);w.viewport_changed(force=True);settle()
faces=w.width_overlay.outlines.get_facecolors()
assert len(faces)==3 and np.all((faces[:,3]>.09)&(faces[:,3]<=.20))
for k,cb in w.visible_boxes.items():
    if k!=refs[0][0]:cb.setChecked(False)
settle()
points=[w.geometry_cache.layers[k]['geometry'][i].representative_point().coords[0] for k,i in refs]
with patch('die_shift_studio.width_analysis.WidthAnalyzer._measure',side_effect=AssertionError('selection recalculated geometry')),patch('die_shift_studio.width_summary.summarize',side_effect=AssertionError('selection rescanned extremes')):
    click(points[0]);click(points[1],keys.ShiftModifier);click(points[2],keys.ShiftModifier)
    typ=w.width_overlay;assert set(w.selection.selected)==set(refs)
    for ref in refs:
        item=summary['witnesses'][ref]
        assert f'{ref[0]} #{ref[1]+1} ·' in labels() and typ.label_text(item,w.view_settings.width_label_decimals) in labels()
        assert pose_text(summary['grid_key'],item['index']) in labels()
    assert w.extreme_table.currentRow()==0 and not np.any(w.shown_states)
    if os.environ.get('DSA_V29_SCREENSHOT'):w.grab().save(os.environ['DSA_V29_SCREENSHOT'])
    # A multi-selection persists when panning its source lines out of view.
    w.set_view((4000,4000),350,180);w.viewport_changed(force=True);settle()
    assert set(w.selection.selected)==set(refs) and not hasattr(w,'width_label_overflow')
    assert labels().count('线路在当前视野外')==len(refs)
    w.canvas.draw()
    for a in w.width_overlay.labels:
        if not a.get_visible():continue
        point=w.ax.transData.transform(a.xy)
        assert min(abs(point[0]-w.ax.bbox.x0),abs(point[0]-w.ax.bbox.x1),abs(point[1]-w.ax.bbox.y0),abs(point[1]-w.ax.bbox.y1))<2
        extent=a.get_bbox_patch().get_window_extent(w.canvas.get_renderer())
        assert w.ax.bbox.contains(extent.x0,extent.y0) and w.ax.bbox.contains(extent.x1,extent.y1)
    for ref in refs:assert f'{ref[0]} #{ref[1]+1} ·' in labels()
    w.set_view((0,0),380,170);w.viewport_changed(force=True);settle()
    # A small viewport must still expose every selected label without overlap.
    with patch('die_shift_studio.width_labels.LabelLayout.place_selected',return_value=None):
        click(points[1],keys.ControlModifier);click(points[1],keys.ShiftModifier)
        assert not hasattr(w,'width_label_overflow') and any(a.get_visible() for a in w.width_overlay.labels)
        for ref in refs:assert f'{ref[0]} #{ref[1]+1} ·' in labels()
    click(points[1],keys.ControlModifier)
    assert f'{refs[1][0]} #{refs[1][1]+1} ·' not in labels() and f'{refs[0][0]} #{refs[0][1]+1} ·' in labels()
    QTest.keyClick(w.canvas,getattr(Qt,'Key',Qt).Key_Escape);settle();assert not labels().strip()
    mode=w.setting_fields['width_display'].boxes['固定RDL'];mode.setCurrentIndex(mode.findData('hide'));settle()
    click(points[0]);assert w.selection.selected==[refs[0]] and '工况' in labels()
    w.visible_boxes[refs[0][0]].setChecked(False);settle();assert not labels().strip()
    w.visible_boxes[refs[0][0]].setChecked(True);settle();assert '工况' in labels()
    mode.setCurrentIndex(mode.findData('show'));settle()
    click([150,60]);assert not w.selection.selected and not labels().strip()
w.problem_filter.setChecked(False);settle();table=w.extreme_table
table.setCurrentCell(1,0);settle();QTest.keyClick(table,getattr(Qt,'Key',Qt).Key_Down);settle()
assert table.currentRow()==2 and not w.current_frame.get('nominal_global')
# V36 extends probability-weighted fill to concrete poses, retaining fixed alpha.
faces=w.width_overlay.outlines.get_facecolors()
assert not len(faces) or np.allclose(faces[:,3],.20)
# Check the actually painted border on every side, including the span across column joins.
view=table.viewport();pm=view.grab();im=pm.toImage();scale=pm.devicePixelRatio()
def ink(x,y):
    color=im.pixelColor(round(x*scale),round(y*scale))
    return max(abs(a-b) for a,b in zip(color.getRgb()[:3],(97,133,164)))<40
y=table.rowViewportPosition(2);h=table.rowHeight(2);last=min(view.width(),sum(table.columnWidth(i) for i in range(table.columnCount())))
assert all(ink(x,y+1) and ink(x,y+h-1) for x in (8,table.columnWidth(0),last-7))
assert ink(1,y+h//2) and ink(last-1,y+h//2)
assert not ink(8,table.rowViewportPosition(1)+1)
for safe in (True,False):
    values=[w.cache_row_color(n,safe).lightness() for n in (0,1,4,20,2000)]
    assert values==sorted(values,reverse=True) and min(values)>225 and values[1]>values[2]
name='示例P_单点PATH与相邻孔盘';q=json.loads((ROOT/'examples'/(name+'.json')).read_text())
q['gds_path']=str(ROOT/'examples'/q['gds_path']);q['parameters'].update(target_nsigma='Infinity')
q.setdefault('settings',{}).update(animation_enabled=False);q['overlays']={'width':True}
src=root/'edge.dshift.json';src.write_text(json.dumps(q));w.load_project(src);settle();w.run_evaluation();settle()
summary=w.current_frame['width_summary'];ref,item=next((r,v) for r,v in summary['witnesses'].items() if v['kind']=='edge')
for k,cb in w.visible_boxes.items():
    if k!=ref[0]:cb.setChecked(False)
poly=w.geometry_cache.layers[ref[0]]['geometry'][ref[1]];point=poly.representative_point().coords[0]
w.set_view(point,240,160);w.viewport_changed(force=True);settle();click(point)
assert ref in w.selection.selected and '封装边距' in labels() and '要求' in labels() and '不足' in labels() and '必过:' not in labels()
assert pose_text(summary['grid_key'],item['index']) in labels()
w.close();app.processEvents();tmp.cleanup()
print('GUI_V29_PASS real multi-select and remove, replayable offsets, zero-only fills, hidden sources and four-sided row border')
