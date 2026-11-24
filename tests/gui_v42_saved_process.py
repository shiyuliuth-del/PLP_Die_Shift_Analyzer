"""Persist completed analysis across independent interpreter/Qt processes."""
import os,sys,json,time,tempfile,subprocess,hashlib
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]

def child(mode,folder):
 import numpy as np
 from unittest.mock import patch
 from die_shift_studio.gui import MainWindow,W
 from die_shift_studio.reconstruction import Reconstructor
 app=W.QApplication([]);w=MainWindow(jobs_dir=folder/'Jobs');w.show();errors=[];w.error=lambda value:errors.append(str(value))
 def wait():
  end=time.monotonic()+150
  while time.monotonic()<end:
   app.processEvents()
   if not (w._tasks or w.preview_pending or w.motion_pending or w.coverage_timer.isActive() or w._coverage_pending or w.viewport_timer.isActive() or w.transition.timer.isActive() or w.width_yield.timer.isActive() or w.reconstruction_yield.timer.isActive()):break
   time.sleep(.005)
  else:raise AssertionError(w.log_bus.text()[-4000:])
  assert not errors,errors
  assert '[ERROR]' not in w.log_bus.text(),w.log_bus.text()[-4000:]
 def copper():return hashlib.sha256(w.current_frame['reconstruction']['replacements']['5/0'][0]['geometry'].wkb).hexdigest()
 saved=folder/'completed.dshift.json'
 try:
  if mode=='save':
   import gdstk
   from dataclasses import asdict
   from test_v38 import fixture
   e,data,_=fixture();roles=e.roles;p=e.p;e.close();lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('TOP')
   for key,rows in data.layers.items():
    layer,dt=map(int,key.split('/'));cell.add(*(gdstk.Polygon(xy,layer=layer,datatype=dt) for xy in rows))
   source=folder/'case.gds';lib.write_gds(source)
   record=dict(schema_version=5,gds_path=str(source),top_cell='TOP',roles=roles,layer_order=['6/0','5/0','4/0','3/0','128/0','127/0'],parameters=asdict(p),settings=dict(animation_enabled=False,safe_box_budget=32),overlays=dict(width=True,reconstruction=True))
   project=folder/'initial.dshift.json';project.write_text(json.dumps(record));w.load_project(project);wait()
   w.manual_states=np.array([[8.,5.,.003]]);w.request_preview();wait();w.write_project(saved)
   assert json.loads(saved.read_text()).get('saved_results')
   (folder/'expected.json').write_text(json.dumps(dict(copper=copper(),states=w.current_frame['states'].tolist())))
  else:
   expected=json.loads((folder/'expected.json').read_text())
   with patch.object(Reconstructor,'frame',side_effect=AssertionError('saved frame was recalculated in fresh process')):
    w.load_project(saved);wait()
    assert w.result is not None and w.current_frame is not None
    assert copper()==expected['copper'];np.testing.assert_array_equal(w.current_frame['states'],expected['states'])
    assert w.width_yield.bank is not None and w.reconstruction_yield.evidence is not None
  print('FRESH_PROCESS_'+mode.upper()+'_PASS')
 finally:w.close();wait();app.processEvents()

if __name__=='__main__':
 if len(sys.argv)==3:child(sys.argv[1],Path(sys.argv[2]))
 else:
  with tempfile.TemporaryDirectory() as folder:
   for mode in ('save','restore'):
    result=subprocess.run([sys.executable,__file__,mode,folder],cwd=ROOT,env=os.environ.copy(),capture_output=True,text=True,timeout=180)
    print(result.stdout);assert result.returncode==0,result.stdout+result.stderr
  print('GUI_V42_FRESH_PROCESS_PASS: actual new interpreter restores complete frame and statistics without reconstruction')
