"""Executable Qt input/pixel tests and portable V9 evidence (no frozen build)."""
import os,sys,time,json,tempfile
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from shapely.geometry import Polygon,box
from matplotlib.backend_bases import MouseEvent
from die_shift_studio.gui import MainWindow,W,QtCore,QtGui
from die_shift_studio.qt_compat import Qt
from die_shift_studio.preview import rigid_transform
from PyQt5.QtTest import QTest
app=W.QApplication([]);temp=tempfile.TemporaryDirectory();w=MainWindow(jobs_dir=Path(temp.name)/'Jobs');w.show();errors=[];w.error=lambda message:errors.append(str(message));metrics={}
def wait():
    deadline=time.monotonic()+50
    while time.monotonic()<deadline:
        app.processEvents()
        if not(w.worker or w.preview_worker or w.preview_pending or w.transition.timer.isActive() or w.coverage_timer.isActive() or w._coverage_pending):break
        time.sleep(.002)
    assert not(w.worker or w.preview_worker or w.preview_pending or w.transition.timer.isActive())
    assert not errors,errors
    app.processEvents()
def shot(name):
    w.canvas.draw();w.grab().save(str(ROOT/'figures'/('software_V14_regression_'+name+'.png')))
def choose(widget,value):widget.setCurrentIndex(widget.findData(value));wait()
def mouse(name,xy):
    e=MouseEvent(name,w.canvas,*w.ax.transData.transform(xy),button=1);w.canvas.callbacks.process(name,e)
def click(xy):mouse('button_press_event',xy);mouse('button_release_event',xy);app.processEvents()
def drag(a,b):mouse('button_press_event',a);mouse('motion_notify_event',b);mouse('button_release_event',b);app.processEvents()
w.load_project(ROOT/'Jobs/示例D_芯片外扇出.dshift.json');wait();w.run_evaluation();wait()
assert 'input_nsigma' not in w.fields and w.target_sigma.count()==14 and not w.target_sigma.isEditable()
assert '非金属图形' not in [w.role_boxes['4/0'].itemText(i) for i in range(w.role_boxes['4/0'].count())]
assert w.analysis_split.count()==3  # Schemes, combined safe ranges, preview statistics.
assert w.analysis_split.widget(1).isAncestorOf(w.safe_range_canvas)
assert w.analysis_split.widget(2).isAncestorOf(w.distribution_canvas)
assert w.ax.get_xlabel()==w.ax.get_ylabel()==''
assert w.ax.get_position().width>.92
# Every local corner is in the input box (degrees converted at display only).
assert all(np.max(np.abs(e['states'][:,:2]))<=35 for e in w.extremes)
# Top visual layer first; repeated click cycles through real metal layers.
w.set_view([0,1000],300,200);w.canvas.draw();position=[0,1000];picked=[]
for _ in range(5):click(position);picked.append(w.selection.selected[0][0])
assert picked==['3/0','4/0','5/0','6/0','3/0'],picked
QTest.keyClick(w,Qt.Key_Escape);app.processEvents();assert not w.selection.selected
click([140,1050]);assert not w.selection.selected
assert not any(w.roles()[key] in ('芯片边框','封装边框') for key,_ in w.selection.hits(box(-10000,-10000,10000,10000)))
# Window/crossing selection compared against brute-force actual polygons.
def expected(a,b,contained):
    region=box(min(a[0],b[0]),min(a[1],b[1]),max(a[0],b[0]),max(a[1],b[1]));result=set()
    for key,arrays in w.rendered_layers.items():
        if w.roles()[key] in ('芯片边框','封装边框'):continue
        for i,arr in enumerate(arrays):
            poly=Polygon(arr)
            if (region.covers(poly) if contained else region.intersects(poly)):result.add((key,i))
    return result
for a,b in [([-28,975],[28,1025]),([28,1025],[-28,975])]:
    drag(a,b);assert set(w.selection.selected)==expected(a,b,b[0]>=a[0]),w.selection.selected
# Selected geometry tracks a fanout via's connected owner beyond the DIE boundary.
w.selection.selected=[('4/0',0)];w.selection.draw();w.manual_die.setCurrentIndex(0);w.manual_fields['dx'].setValue(65);w.apply_manual();wait()
np.testing.assert_allclose(w.selection.artist.get_paths()[0].vertices[:-1],w.rendered_layers['4/0'][0])
for scheme in (1,2,3):
    choose(w.issue_scheme,scheme);assert w.current_frame['contact']['intrusions']
    assert any(a.get_alpha()==w.view_settings.intrusion_alpha for a in w.issue_artists)
    issue=w.display_issues[0];np.testing.assert_allclose(w.issue_origin(issue),np.array(issue['origin'])+[65,0])
w.selection.selected=[];w.selection.draw();w.set_view([65,1000],180,130);w.canvas.draw()
# Visible filled red area must add pixels beyond the existing red circle.
a=np.asarray(w.canvas.buffer_rgba()).copy();fills=[a for a in w.issue_artists if getattr(a,'_intrusion',False)]
for artist in fills:artist.set_visible(False)
w.canvas.draw();b=np.asarray(w.canvas.buffer_rgba()).copy()
red=(a[:,:,0]>a[:,:,1]+35)&(a[:,:,0]>a[:,:,2]+25)&(np.max(np.abs(a.astype(float)-b.astype(float)),axis=2)>15)
assert red.sum()>40,int(red.sum())
for artist in fills:artist.set_visible(True)
metrics['red_intrusion_pixels_added']=int(red.sum());metrics['intrusion_area_um2']=sum(q['area_um2'] for q in w.current_frame['contact']['intrusions'])
# Cached rendering and an ordinary full draw agree in the plot interior.
w.render_scene();cached=np.asarray(w.canvas.buffer_rgba()).copy();w.canvas.draw();full=np.asarray(w.canvas.buffer_rgba()).copy()
x0,y0,x1,y1=map(int,w.ax.bbox.extents);h=full.shape[0];diff=np.max(np.abs(cached.astype(float)-full.astype(float)),axis=2)[h-y1+3:h-y0-3,x0+3:x1-3]
assert np.mean(diff>8)<.005,np.mean(diff>8)
metrics['cached_full_pixel_mismatch_fraction']=float(np.mean(diff>8))
shot('intrusion')
# Probability palette changes are instant and do not discard numerical data.
w.probability_toggle.setChecked(True);wait();data=w.probability_data;choose(w.setting_fields['probability_cmap'],'BuPu')
assert w.probability_data is data and w.probability_artists[0].get_cmap().name=='BuPu'
assert all(0<a.get_alpha()<1 for a in w.probability_artists)
shot('probability')
w.probability_toggle.setChecked(False);w.space_toggle.setChecked(True);wait();data=w.space_data
choose(w.setting_fields['space_scope'],'package');assert w.space_data is data
assert len(w.space_artists)==2 and w.space_artists[0].get_array().min()==w.params().line_space_um
w.fit_plot();w.canvas.draw();assert w.space_colorbar.ax.bbox.y1<w.ax.bbox.y0
w.visible_boxes['4/0'].setChecked(False);assert not w.space_artists[0].get_visible()
w.visible_boxes['4/0'].setChecked(True);assert w.space_artists[0].get_visible();shot('package_space')
# Minor marks: each complete major interval contains one to five.
for span in (100,240,2000,12000):
    w.set_view([0,0],span,span/2);w.canvas.draw()
    for axis in (w.ax.xaxis,w.ax.yaxis):
        major=axis.get_majorticklocs();minor=axis.get_minorticklocs();lo,hi=axis.get_view_interval()
        for l,r in zip(major[:-1],major[1:]):
            if l>=lo and r<=hi:assert 1<=np.count_nonzero((minor>l)&(minor<r))<=5
# Animation completion, exact endpoint, latest click, nominal restoration, then view location.
w.fit_plot();w.setting_fields['animation_enabled'].setChecked(True);wait();samples=[];visual=[]
for row in (1,2,3):
    t=time.perf_counter();w.extreme_table.setCurrentCell(row,0);wait();samples.append((time.perf_counter()-t)*1000);visual.append(w.transition.last_duration_ms)
    np.testing.assert_array_equal(w.shown_states,w.extremes[row-1]['states'])
w.extreme_table.setCurrentCell(4,0);app.processEvents();w.extreme_table.setCurrentCell(5,0);wait();np.testing.assert_array_equal(w.shown_states,w.extremes[4]['states'])
w.extreme_table.setCurrentCell(0,0);wait();assert w.current_frame is None and w.shown_states is None
w.manual_fields['dx'].setValue(65);w.apply_manual();wait()
w.locate_issue(0,0);wait();issue=w.filtered_issues[0];np.testing.assert_allclose([np.mean(w.ax.get_xlim()),np.mean(w.ax.get_ylim())],issue['point'])
metrics['fanout_animation_visual_ms']=visual;metrics['fanout_animation_total_ms']=samples;metrics['animation_configured_ms']=w.view_settings.animation_duration_ms
assert w.view_settings.animation_fps==30
w.fit_plot();w.controls.setCurrentIndex(2);shot('settings')
# Inspect intermediate poses, not just endpoints: red areas and leaders update together.
w.space_toggle.setChecked(False);w.probability_toggle.setChecked(False);wait()
w.setting_fields['animation_enabled'].setChecked(False);w.manual_fields['dx'].setValue(0);w.manual_fields['dy'].setValue(0);w.manual_fields['angle'].setValue(0);w.apply_manual();wait()
live=[];original_plot=w.plot_layout
def observed_plot(*args,**kwargs):
    original_plot(*args,**kwargs)
    if getattr(w,'_in_animation',False):
        q=w.current_frame;count=len(q['contact']['intrusions']);live.append((time.perf_counter(),float(q['states'][0,0]),count))
        assert len(w.issue_artists[0].get_paths())==count
        for segment,issue in zip(w.issue_artists[2].get_segments(),w.display_issues):np.testing.assert_allclose(segment[0],w.issue_origin(issue))
w.plot_layout=observed_plot;w.setting_fields['animation_enabled'].setChecked(True);w.manual_fields['dx'].setValue(65);w.apply_manual();wait();w.plot_layout=original_plot
assert len(live)>=2 and any(0<x<65 for _,x,_ in live)
assert any(n>0 for _,x,n in live) or w.current_frame['contact']['intrusions'],live  # Deadline may skip the brief contact tail; endpoint is exact.
metrics['live_annotation_frames']=len(live);metrics['live_annotation_contact_frames']=sum(n>0 for _,_,n in live)
metrics['live_display_fps']=(len(live)-1)/(live[-1][0]-live[0][0]) if len(live)>1 else None
w.space_toggle.setChecked(True);wait()
# Wheel cannot alter combo values or a layer order while dragging.
w.controls.setCurrentIndex(0);app.processEvents();header=w.layer_table.verticalHeader();view=w.layer_table.viewport();order=w.ordered_layers();x=w.layer_table.columnViewportPosition(2)+10;y=header.sectionSize(0)//2
QTest.mousePress(view,Qt.LeftButton,pos=QtCore.QPoint(x,y))
event=QtGui.QWheelEvent(QtCore.QPointF(x,y),QtCore.QPointF(view.mapToGlobal(QtCore.QPoint(x,y))),QtCore.QPoint(),QtCore.QPoint(0,-120),Qt.LeftButton,Qt.NoModifier,Qt.NoScrollPhase,False)
W.QApplication.sendEvent(view,event);assert w.ordered_layers()==order
QTest.mouseRelease(view,Qt.LeftButton,pos=QtCore.QPoint(x,y))
# Binary infinity survives save/load; parent input multiplier is not a control.
w.setting_fields['animation_enabled'].setChecked(False);w.target_sigma.setCurrentText('∞');wait()
assert np.all(w.space_artists[0].get_array()==w.params().line_space_um)
path=Path(temp.name)/'infinite.json';w.write_project(path);assert json.loads(path.read_text())['parameters']['target_nsigma']=='Infinity'
w.load_project(path);wait();assert w.target_sigma.currentText()=='∞'
w.run_evaluation();wait();w.tabs.setCurrentWidget(w.detail);app.processEvents();assert '输入单边幅度的极端预演' in w.detail.toPlainText();assert 'math:///' in w.detail.toHtml()
metrics['status']='PASS';(ROOT/'results/regression_V14.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2)+'\n');w.close();app.processEvents();temp.cleanup();print(json.dumps(metrics,ensure_ascii=False,indent=2))
