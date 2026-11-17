"""V47 idle/pending IPC, command-only work, saved UI preferences and clean exit."""
import os,sys,time,tempfile,json,threading
from pathlib import Path
from dataclasses import asdict
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent)]
import gdstk
from die_shift_studio.gui import MainWindow,W
from qt_shutdown import close_window
from test_v38 import fixture
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();folder=Path(tmp.name)
w=MainWindow(jobs_dir=folder/'Jobs');w.show();w.resize(1400,1050);errors=[];w.error=lambda text:errors.append(str(text))
w.requested_ipc_endpoint=folder/'ipc.json'
def until(predicate,seconds=120):
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  app.processEvents();time.sleep(.005)
  if predicate():return
 raise AssertionError(w.runtime_view.toPlainText()[-4000:])
def idle():until(lambda:not (w._tasks or w.worker or w.preview_workers or w.measured_output.running or w.measured_output.transition or w.measured_output.stopping))
def done(reply):
 key=reply['task_id'];until(lambda:w.measured_output.jobs[key]['status'] in ('completed','error','cancelled'))
 assert w.measured_output.jobs[key]['status']=='completed',w.measured_output.jobs[key]
 return w.measured_output.jobs[key]['result']
try:
 idle();m=w.measured_output
 assert not m.settings_header.isChecked() and m.settings_body.isHidden()
 assert w.open_gds_button.objectName()!='Primary'
 assert not any('运动层按下层连接' in q.text() for q in w.findChildren(W.QLabel))
 gate=threading.Event()
 w.start_worker(lambda progress:gate.wait(10),lambda _:None)
 m.toggle.setChecked(True)
 assert m.pending_enable and not m.toggle.isEnabled() and not m.active()
 gate.set();until(m.active);idle()
 assert w.data is None and not m.ready and m.command('ping',{})['result']['enabled']
 assert not errors,errors
 e,data,_=fixture();roles=e.roles;p=e.p;e.close()
 lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('TOP')
 for key,rows in data.layers.items():
  layer,dt=map(int,key.split('/'));cell.add(*(gdstk.Polygon(xy,layer=layer,datatype=dt) for xy in rows))
 source=folder/'drawing.gds';lib.write_gds(source);project=folder/'input.dshift.json'
 project.write_text(json.dumps(dict(schema_version=5,gds_path=str(source),top_cell='TOP',roles=roles,layer_order=['6/0','5/0','4/0','3/0'],parameters=asdict(p),settings={'animation_enabled':False},overlays={'reconstruction':True,'width':True})))
 with patch('die_shift_studio.gui.evaluate',side_effect=AssertionError('full assessment during IPC')),patch('die_shift_studio.reconstruction_status.ReconstructionYieldDisplay.start',side_effect=AssertionError('yield during IPC')):
  done(m.command('project.open',{'path':str(project)}));idle()
  assert m.ready and not m.session.instances and w.result is None
  assert not w.width_toggle.isChecked() and not w.reconstruction_toggle.isChecked()
  w.request_preview();w.run_evaluation();w.analysis.activate();idle()
  assert not m.session.instances
  details=done(m.command('preview.submit',{'dies':[dict(die=1,dx_um=2,dy_um=1,angle_deg=0)]}));idle()
  assert details['fixed_rdl_drc_checked'] and w.current_frame is not None
  assert w.result is None and w.reconstruction_yield.bank is None and w.width_yield.bank is None
  assert not m.command('info',{})['result']['running']
  m.command('project.close',{})
  assert not m.ready and w.data is None
 m.toggle.setChecked(False);idle();assert not w.remote_locked
 # Startup loading suppresses saved overlays and result restoration BEFORE enable.
 w.requested_measured=True;w.load_project(project);idle()
 assert w.result is None and not w.reconstruction_toggle.isChecked()
 m.toggle.setChecked(True);until(lambda:m.ready);idle()
 saved=json.loads(project.read_text());assert saved['overlays']['reconstruction'] and saved['overlays']['width']
 # Return-to-editor behavior remains intentional after disable. Clear the saved
 # overlays in the test to isolate persistence/layout from unrelated analysis.
 m.saved_overlays={k:False for k in m.saved_overlays};m.toggle.setChecked(False);idle()
 w.layer_split.setSizes([180,500]);app.processEvents();height=w.layer_split.sizes()[0]
 w.measured_output.settings_header.setChecked(True);w.write_project(project)
 saved=json.loads(project.read_text());assert saved['layer_table_height']==height
 assert '实测输出设置' in saved['expanded_settings']
 w.load_project(project);idle();assert w.measured_output.settings_header.isChecked()
 assert abs(w.layer_split.sizes()[0]-height)<12,(w.layer_split.sizes(),height)
 assert not errors,errors
 assert '未处理异常' not in w.runtime_view.toPlainText()
 print('GUI_V47_PASS: pending/empty IPC, lazy commands, no assessment/yield, saved overlays, splitter and collapse')
finally:
 close_window(app,w);tmp.cleanup()
