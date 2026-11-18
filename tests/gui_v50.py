"""V50 actual Qt controls, manual compute, rulers and project-isolated artists."""
import os,sys,time,tempfile,json
from pathlib import Path
from dataclasses import asdict
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import numpy as np,gdstk,shapely
from matplotlib.backend_bases import MouseEvent
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.qt_compat import Qt,QtCore,BINDING
from test_v49_reconstruction import V49ReconstructionTests
from qt_shutdown import close_window
if BINDING=='PyQt6':from PyQt6.QtTest import QTest
else:from PyQt5.QtTest import QTest
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();root=Path(tmp.name);w=MainWindow(jobs_dir=root/'Jobs');w.resize(1600,1000);w.show();errors=[];w.error=lambda x:errors.append(str(x))
def wait():
 end=time.monotonic()+240
 while time.monotonic()<end:
  app.processEvents();time.sleep(.005)
  if not (w._tasks or w.preview_pending or w.reconstruction_yield.pending or w.reconstruction_yield.timer.isActive() or w.coverage_timer.isActive()):return
 raise AssertionError(w.runtime_view.toPlainText()[-4000:])
def event(name,p,button=1,key=None,step=0):
 x,y=w.ax.transData.transform(p);e=MouseEvent(name,w.canvas,x,y,button=button,key=key,step=step);w.canvas.callbacks.process(name,e);app.processEvents()
try:
 e,data,case=V49ReconstructionTests().fixture();roles=e.roles;p=e.p;e.close()
 lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('TOP')
 for layer,rows in data.layers.items():
  l,d=map(int,layer.split('/'));cell.add(*(gdstk.Polygon(xy,layer=l,datatype=d) for xy in rows))
 gds=root/'case.gds';lib.write_gds(gds);job=root/'job.dshift.json'
 job.write_text(json.dumps(dict(schema_version=5,gds_path=str(gds),top_cell='TOP',roles=roles,layer_order=['6/0','5/0','4/0','3/0','128/0','127/0'],parameters=asdict(p),settings=dict(animation_enabled=False,reconstruction_boundary_mode='perpendicular'),overlays={})))
 w.load_project(job);wait();w.run_evaluation();wait();w.problem_filter.setCurrentIndex(0);wait()
 assert all(name in w.setting_sections for name in ('结果分析','DRC','GDS/OAS输出参数','DXF输出参数','实测输出参数'))
 assert '安全范围' not in w.setting_sections and '无接触良率' not in w.setting_sections
 assert not any(w.setting_sections[name].isChecked() for name in ('GDS/OAS输出参数','DXF输出参数','实测输出参数'))
 w.canvas.draw();w.canvas.setFocus();app.processEvents();w.rulers.enter()
 # Key events are delivered by Qt, including in ruler mode. Distances are
 # fractions of the viewport, so layout units and zoom level do not matter.
 keys=getattr(Qt,'Key',Qt);limits=lambda:np.array([w.ax.get_xlim(),w.ax.get_ylim()]);base=limits();span=base[:,1]-base[:,0]
 for name,dx,dy in [('Right',1,0),('Up',0,1),('Left',-1,0),('Down',0,-1)]:
  before=limits();QTest.keyClick(w.canvas,getattr(keys,'Key_'+name));app.processEvents();np.testing.assert_allclose(limits(),before+(.2*span*np.array([dx,dy]))[:,None],atol=1e-7)
 np.testing.assert_allclose(limits(),base,atol=1e-7)
 w.extreme_table.setFocus();app.processEvents();QTest.keyClick(w.extreme_table,keys.Key_Down);app.processEvents();np.testing.assert_allclose(limits(),base,atol=1e-7)
 w.extreme_table.selectRow(0);wait();w.canvas.setFocus();app.processEvents()
 event('button_press_event',(0,0));anchor=w.rulers.start.copy();before=limits();event('button_press_event',(0,0),2);event('motion_notify_event',(5,5),2);event('button_release_event',(5,5),2)
 assert not np.allclose(limits(),before);np.testing.assert_equal(w.rulers.start,anchor);assert not w.rulers.rows
 w.rulers.clear();w.ax.set_xlim(base[0]);w.ax.set_ylim(base[1]);w.canvas.draw()
 event('button_press_event',(-14.9,10.1));event('button_release_event',(-14.9,10.1));assert not w.rulers.rows
 event('motion_notify_event',(20,30),key='shift');event('button_press_event',(20,30),key='shift');event('button_release_event',(20,30),key='shift')
 np.testing.assert_allclose(w.rulers.rows[0][0],[-15,10],atol=.1);assert abs(w.rulers.rows[0][1][1]-10)<.1
 event('button_press_event',(150,100));event('button_release_event',(150,100));event('motion_notify_event',(190,135),key='ctrl');event('button_press_event',(190,135),key='ctrl');event('button_release_event',(190,135),key='ctrl')
 v=np.subtract(w.rulers.rows[1][1],w.rulers.rows[1][0]);assert abs(abs(v[0])-abs(v[1]))<1e-6
 w.rulers.exit();w.reconstruction_toggle.setChecked(True);wait();old=w.current_frame;ticket=w.preview_ticket
 w.setting_fields['reconstruction_auto'].setChecked(False);app.processEvents()
 q=next(q for q in old['reconstruction']['pieces'] if q.get('route_plan') is not None and q['moved_cut'].geom_type=='LineString' and q['extra_cut'].geom_type=='LineString')
 original_interval=q['interval']
 for side,kind,delta in ((0,'moved_cut',10),(1,'extra_cut',-10)):
  xy=np.asarray(q[kind].centroid.coords[0]);w.set_view(xy,400,240);w.plot_layout(preserve=True);w.canvas.draw()
  event('button_press_event',xy);assert w.cut_drag.state is not None
  target=xy+q['route_plan'].tangent(q['interval'][side])*delta
  event('motion_notify_event',target);w.cut_drag._tick();event('button_release_event',target)
  assert w.current_frame is old and w.reconstruction_pending
 staged=next(row for row in w.view_settings.reconstruction_intervals if row[4]==q['route_plan'].identity)
 assert abs(staged[-2]-original_interval[0])>1 and abs(staged[-1]-original_interval[1])>1
 style=w.setting_fields['reconstruction_style'];style.setCurrentIndex(style.findData('c1'));app.processEvents()
 assert w.reconstruction_pending and w.current_frame is old and not w.preview_pending
 w.reconstruction_compute.click();wait();assert not w.reconstruction_pending
 assert w.current_frame['reconstruction']['configuration']==w.view_settings.reconstruction_key()
 assert w.preview_ticket>ticket
 # Hiding one category leaves the completed numerical catalogue intact.
 w.setting_fields['reconstruction_auto'].setChecked(True);wait()
 from dataclasses import replace
 row=dict(kind='线宽不足',layer='5/0',layers=('5/0',),geometry=shapely.box(10,0,20,10),point=[15,5],required_um=15,actual_um=10)
 w.current_frame=dict(w.current_frame,drc_locations=[row]);w._issue_table_stamp=None;w.fill_issues()
 assert w.issue_table.columnCount()==6 and w.issue_table.horizontalHeaderItem(3).text()=='实际'
 assert w.issue_table.item(0,3).text()=='10.000 μm'
 w.view_settings=replace(w.view_settings,drc_visible=());w.fill_issues();assert w.issue_table.rowCount()==0 and len(w.current_frame['drc_locations'])==1
 w.view_settings=replace(w.view_settings,drc_visible=('width',));w.fill_issues();assert w.issue_table.rowCount()==1
 w.write_project(job)
 # Old hover/point Lines must not survive a new project; remove actual artists.
 overlay=w.reconstruction_overlay;overlay.points.set_data([11],[22]);overlay.points.set_visible(True);previous=overlay.points
 new=root/'new.dshift.json';record=json.loads(job.read_text());record['overlays']={};record.pop('saved_results',None);new.write_text(json.dumps(record))
 w.load_project(new);wait();assert previous.axes is None and not overlay.pulse_timer.isActive()
 assert w.view_settings.drc_visible==('width',);assert len(w.rulers.rows)==2
 assert not errors,errors
 print('GUI_V50_PASS native output controls, manual compute, 2-click snapping/axis rulers, actual DRC column, display-only categories, isolated project artists',flush=True)
finally:close_window(app,w);tmp.cleanup()
