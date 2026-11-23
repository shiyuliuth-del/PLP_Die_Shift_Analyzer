"""Real Qt assessment gating, safe sidecar restore, controls and zero-only export."""
import os,sys,time,json,tempfile
from pathlib import Path
from dataclasses import asdict,replace
from unittest.mock import patch
import numpy as np,gdstk
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.reconstruction import Reconstructor
from die_shift_studio.qt_compat import QtCore
from test_v38 import fixture
app=W.QApplication([]);temp=tempfile.TemporaryDirectory();folder=Path(temp.name);w=MainWindow(jobs_dir=folder/'Jobs');w.resize(1400,950);w.show();errors=[];w.error=lambda text:errors.append(str(text))
def wait():
 until=time.monotonic()+180
 while time.monotonic()<until:
  app.processEvents()
  if not w._tasks and not w.coverage_timer.isActive() and not w._coverage_pending and not w.preview_pending and not w.motion_pending and not w.width_yield.timer.isActive() and not w.reconstruction_yield.timer.isActive() and not w.viewport_timer.isActive():break
  time.sleep(.005)
 else:raise AssertionError(w.runtime_view.toPlainText()[-3000:])
 assert not errors,errors
 assert '未处理异常' not in w.runtime_view.toPlainText(),w.runtime_view.toPlainText()[-3000:]
try:
 e,d,c=fixture();p=e.p;roles=e.roles;e.close();lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('TOP')
 for name,rows in d.layers.items():
  layer,dt=map(int,name.split('/'));cell.add(*(gdstk.Polygon(xy,layer=layer,datatype=dt) for xy in rows))
 gds=folder/'case.gds';lib.write_gds(gds)
 job=dict(schema_version=5,gds_path=str(gds),roles=roles,top_cell='TOP',layer_order=['6/0','5/0','4/0','3/0','128/0','127/0'],parameters=asdict(p),settings=dict(animation_enabled=False,result_cache_directory=str(folder/'saved'),safe_box_budget=32),overlays=dict(width=True,reconstruction=True))
 path=folder/'source.dshift.json';path.write_text(json.dumps(job));w.load_project(path);wait()
 assert w.result is not None and w.extremes is not None
 assert not hasattr(w,'repair_toggle')
 assert w.fields['package_clearance_um'].value()==10
 assert w.view_settings.reconstruction_boundary_mode=='wide'
 assert w.setting_fields['reconstruction_box_mode'].currentData()=='aligned'
 assert '接触标记' not in w.setting_sections
 process=w.fields['minimum_edge_um'].parentWidget()
 assert '其他' in w.process_sections
 assert all(w.setting_fields[key].parentWidget() is process for key in ('drc_min_angle_deg','drc_tolerance_um'))
 drc_row=w._drc_policy_editors[1].boxes['width'].parentWidget()
 assert w.setting_fields['contact_marker_size'].parentWidget() is drc_row.parentWidget()
 w.manual_states=np.array([[8.,5.,.003]]);w.request_preview();wait()
 frame=w.current_frame;w.show_issues.setChecked(False);w.plot_layout(preserve=True)
 assert not any(a.get_visible() for a in (w.drc_overlay.area,w.drc_overlay.points,w.drc_overlay.links))
 w.show_issues.setChecked(True);wait()
 # A queued statistics task is not a completed result, even before its
 # worker appears in the active-task registry.
 from die_shift_studio.jobs import job_path, PROJECT_DIRECTORIES
 queued=job_path(w.jobs_dir,'queued');w.reconstruction_yield.timer.start(2000)
 w.write_project(queued);assert not json.loads(queued.read_text()).get('saved_results')
 assert all((queued.parent/name).is_dir() for name in PROJECT_DIRECTORIES)
 w.reconstruction_yield.timer.stop()
 saved=folder/'restore.dshift.json';w.write_project(saved);doc=json.loads(saved.read_text());assert doc['saved_results'],w.runtime_view.toPlainText()[-3000:]
 assert (saved.parent/doc['saved_results']['path']).is_file()
 expected=w.current_frame['reconstruction']['replacements']['5/0'][0]['geometry'].wkb
 w.close();wait();app.processEvents()
 w=MainWindow(jobs_dir=folder/'Jobs2');w.error=lambda text:errors.append(str(text));w.show()
 with patch.object(Reconstructor,'frame',side_effect=AssertionError('restored pose was recalculated')):
  w.load_project(saved);wait()
  assert w.result is not None and w.extremes is not None and w.current_frame is not None
  assert w.current_frame['reconstruction']['replacements']['5/0'][0]['geometry'].wkb==expected
  assert w.width_yield.bank is not None and w.reconstruction_yield.evidence is not None
 # A checksum-valid but incomplete data snapshot must roll back partial UI
 # application, preserve settings, and chain a real assessment automatically.
 import zlib,hashlib
 from die_shift_studio.project_results import decode,encode
 bad=json.loads(saved.read_text());cache_path=saved.parent/bad['saved_results']['path']
 envelope=json.loads(zlib.decompress(cache_path.read_bytes()));payload=decode(envelope['payload']);del payload['frame']
 envelope['payload']=encode(payload);raw=zlib.compress(json.dumps(envelope).encode());broken=folder/'incomplete.dsresults';broken.write_bytes(raw)
 bad['saved_results']['path']=str(broken);bad['saved_results']['sha256']=hashlib.sha256(raw).hexdigest()
 broken_job=folder/'incomplete.dshift.json';broken_job.write_text(json.dumps(bad));w.load_project(broken_job);wait()
 assert w.result is not None and w.current_frame is not None
 assert w.params().package_clearance_um==10
 assert '未恢复结果缓存' in w.runtime_view.toPlainText()
 # Older empty snapshots must not bypass assessment-first gating.
 payload=decode(envelope['payload']);payload['result']=None;payload['frame']=None
 envelope['payload']=encode(payload);raw=zlib.compress(json.dumps(envelope).encode());broken.write_bytes(raw)
 bad['saved_results']['sha256']=hashlib.sha256(raw).hexdigest();broken_job.write_text(json.dumps(bad))
 w.load_project(broken_job);wait();assert w.result is not None and w.extremes is not None
 # Native dialog contract and real DXF output must be independent of pose.
 from die_shift_studio.export_ui import export_dxf
 from die_shift_studio.qt_compat import ACCEPTED
 from test_v38 import expanded_outlines
 import ezdxf,shapely
 outputs=[]
 for index,pose in enumerate((np.array([[8.,5.,.003]]),np.array([[-10.,-4.,-.004]]))):
  w.manual_states=pose;w.request_preview();wait()
  target=folder/f'zero-{index}.dxf'
  def choose(dialog):dialog.selectFile(str(target));return ACCEPTED
  with patch('die_shift_studio.export_ui.run_dialog',choose):export_dxf(w,regions=True)
  wait();model=ezdxf.readfile(target).modelspace()
  assert model.query('INSERT')
  rows=list(expanded_outlines(model))
  assert all(closed for layer,_,closed in rows if layer in ('PKG_REF','DIE_REF'))
  outputs.append(sorted((layer,tuple(map(tuple,xy)),closed) for layer,xy,closed in rows))
 assert outputs[0]==outputs[1]
 w.manual_states=None;w.request_preview();wait()
 from die_shift_studio.reconstruction_edits import change_zone
 point=(200.,8.);change_zone(w,'add',ref=('5/0',0),point=point);wait()
 assert any(q['nominal_regions']['A'].covers(shapely.Point(point)) for q in w.current_frame['reconstruction']['pieces'])
 print('GUI_V42_PASS assessment-first, saved results without recompute, native zero-only DXF across two poses, clicked-point zone and controls')
finally:
 w.close();wait();app.processEvents();temp.cleanup()
