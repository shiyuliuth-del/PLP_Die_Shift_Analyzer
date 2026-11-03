"""Full-grid paging, cache-aware colours, discrete overlay and old/new Jobs."""
import json,os,sys,tempfile,time
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.qt_compat import QtCore,Qt,BINDING
from die_shift_studio.statistics import ExtremeCatalog
if BINDING=='PyQt6':from PyQt6.QtTest import QTest
else:from PyQt5.QtTest import QTest

app=W.QApplication([]);tmp=tempfile.TemporaryDirectory()
w=MainWindow(jobs_dir=Path(tmp.name)/'Jobs');w.show();errors=[];w.error=lambda msg:errors.append(msg)
def wait():
    deadline=time.monotonic()+180
    while time.monotonic()<deadline:
        app.processEvents()
        if not any((w.worker,w.preview_workers,w.preview_pending,w.hardware_worker,w.transition.timer.isActive(),
                    w.motion_worker,w.viewport_timer.isActive(),w.coverage_timer.isActive(),w._coverage_pending)):break
        time.sleep(.005)
    else:raise AssertionError('Qt task timeout')
    assert not errors,errors
    assert '未处理异常' not in w.runtime_view.toPlainText(),w.runtime_view.toPlainText()[-2500:]
def row_info(row):
    w.refresh_extreme_colors(w.current_frame)
    item=w.extreme_table.item(row,0);tip=item.toolTip();n=int(tip.split('可复用缓存 ')[-1].split(' 项')[0])
    return n,item.background().color().lightness()
name='示例O_运动网络三线路通道';src=ROOT/'Jobs'/(name+'.dshift.json')
w.load_project(src);wait();w.setting_fields['animation_enabled'].setChecked(False);w.width_toggle.setChecked(False);wait()
w.run_evaluation();wait();assert w.extremes.total+1==729 and w.grid_mode.currentText()=='重点组合'
assert w.extreme_table.rowCount()==w.focus_extremes.total+1
w.grid_mode.setCurrentIndex(0);wait();assert w.extreme_table.rowCount()==257
assert w.result['extreme_factors']['checked'] and all(w.extreme_table.item(0,j).text()=='' for j in range(5))
w.page_edit.setText('3');QTest.keyClick(w.page_edit,getattr(Qt,'Key',Qt).Key_Return);wait()
assert w.preview_page==2 and len(w.page_items)==216 and not w.page_next.isEnabled()
index=w.page_items[-1][0];assert index==727
last=w.extreme_table.rowCount()-1;n0,_=row_info(last);assert n0==0
w.extreme_table.setCurrentCell(last,0);wait();n1,l1=row_info(last);assert n1>=1
assert w.active_preview==index and (w.shown_states==w.extremes[index]['states']).all()
w.width_toggle.setChecked(True);wait();n2,l2=row_info(last);assert n2>n1 and l2<l1,(n1,n2,l1,l2)
before=list(w.cache_group.recent);w.refresh_extreme_colors();assert before==list(w.cache_group.recent)
w.grid_mode.setCurrentIndex(1);wait();assert w.active_preview is None
w.grid_mode.setCurrentIndex(0);wait()
w.footprint_toggle.setChecked(True);wait()
assert w.footprint_data and w.footprint_data['poses']>0 and w.footprint_overlay.artist.get_visible()
w.visible_boxes['4/0'].setChecked(False);wait();assert not w.footprint_overlay.artist.get_visible()
w.visible_boxes['4/0'].setChecked(True);wait();assert w.footprint_overlay.artist.get_visible()
w.grid_mode.setCurrentIndex(1);w.show_extreme_page(1);wait();page=w.preview_page
saved=w.jobs_dir/'footprints.dshift.json';w.write_project(saved);record=json.loads(saved.read_text())
assert record['overlays']['footprint'] and record['extreme_view']=={'mode':'focus','page':page+1,'page_size':256}
w.load_project(saved);wait();assert w.footprint_toggle.isChecked() and w.grid_mode.currentIndex()==1 and w.preview_page==page
w.load_project(src);wait();assert not w.footprint_toggle.isChecked()
q=json.loads(src.read_text());q['parameters'].update(per_die_enabled=True,die_bounds=[[0,0,0],[0,7,0]],mean_x_um=5,mean_angle_deg=.1)
q['gds_path']=str(ROOT/'examples'/(name+'.gds'));path=w.jobs_dir/'mixed.dshift.json';path.write_text(json.dumps(q))
w.load_project(path);wait();w.run_evaluation();wait();assert w.result['discrete_states']==3 and w.extreme_table.rowCount()==3
assert all(not e['states'][0].any() and e['states'][1,0]==e['states'][1,2]==0 for e in w.extremes)
w.extreme_table.setCurrentCell(1,0);wait();assert row_info(1)[0]>0
w.clear_cached_results(False);wait()
deadline=time.monotonic()+15
while w.cache_clear_worker is not None and time.monotonic()<deadline:app.processEvents();time.sleep(.005)
assert row_info(1)[0]==0
w.close();app.processEvents();QtCore.QCoreApplication.sendPostedEvents(None,getattr(QtCore.QEvent,'Type',QtCore.QEvent).DeferredDelete);tmp.cleanup()
print('GUI_V26_PASS 729-state pagination, frozen DIE, cache depth, clearing and exact footprint visibility/Job restore.')
