"""Toggle ranges on a real contact pose, save, reload and return to an old job."""
import json,os,sys,tempfile,time,traceback
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.core import read_gds,Parameters,build_case
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.stack import Connections
from die_shift_studio.contact_ranges import RANGES

def main():
    if os.name=='nt':os.environ['QT_QPA_PLATFORM']='windows'
    app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();w=MainWindow(jobs_dir=Path(tmp.name)/'Jobs')
    w.show();app.processEvents()
    path=ROOT/'examples/示例D_芯片外扇出.gds';q=json.loads(path.with_suffix('.json').read_text(encoding='utf8'))
    q['gds_path']=str(path);w.set_data(read_gds(path),q);cache=GeometryCache(w.data)
    con=Connections(w.data,w.roles(),q['layer_order'],cache.geometries);case=build_case(w.data,w.roles(),connections=con)
    engine=PreviewEngine(cache,case,w.roles(),{},Parameters.from_dict(q['parameters']),None,con)
    print('Checking contact contour geometry',file=sys.__stdout__,flush=True)
    frame=engine.frame(np.array([[65.,0,0],[0,0,0]]));w.engine=engine;w.case=case;w.geometry_cache=cache;w.current_frame=frame;w.plot_layout()
    assert frame['contact']['issues'] and frame['contact']['intrusions']
    geometry=tuple((id(g['geometry']),g['area_um2']) for g in frame['contact']['intrusions'])
    toggles=[w.setting_fields[key] for _,key,*_ in RANGES]
    # These controls are inside the normally collapsed settings group.
    with patch.object(w,'request_preview',side_effect=AssertionError('display change started geometry work')):
        for bits in ((True,True),(False,True),(False,False),(True,False)):
            for control,on in zip(toggles,bits):
                if control.isChecked()!=on:control.click()
            assert tuple(a.get_visible() for a in w.zone_artists)==bits
            assert tuple(title in [t.get_text() for t in w.legend.get_texts()] for _,_,title,*_ in RANGES)==bits
            assert all(0<a.get_alpha()<1 for a in w.zone_artists)
            assert w.current_frame is frame and tuple((id(g['geometry']),g['area_um2']) for g in frame['contact']['intrusions'])==geometry
        refs={item['key'] for item in frame['contact']['regions']}
        for key in refs:w.visible_boxes[key].setChecked(False)
        assert not any(a.get_visible() for a in w.zone_artists)
        for key in refs:w.visible_boxes[key].setChecked(True)
        assert tuple(a.get_visible() for a in w.zone_artists)==(True,False)
    # Pressing the plot while the view changes must not starve pending paints.
    w.viewport_timer.stop();w.queue_viewport()
    with patch.object(w.viewport_timer,'start',side_effect=AssertionError('restarted an active timer')):
        w.queue_viewport()
    with patch.object(W.QInputDialog,'getText',return_value=('显示偏好',True)):w.save_project()
    print('Checking saved and legacy Jobs',file=sys.__stdout__,flush=True)
    saved=w.project_path;doc=json.loads(saved.read_text(encoding='utf8'));assert doc['settings']['show_spacing_range'] is False
    for cfg in (doc,q):
        w.set_data(w.data,cfg)
        expected=cfg.get('settings',{}).get('show_spacing_range',True)
        assert w.view_settings.show_spacing_range==expected and toggles[1].isChecked()==expected and toggles[0].isChecked()
    w.close();until=time.monotonic()+30
    while w._tasks and time.monotonic()<until:app.processEvents();time.sleep(.005)
    assert not w._tasks;app.processEvents();tmp.cleanup()
    print('GUI_V30_PASS independent contours, legend, visibility, retained geometry, saved/old Jobs and coalesced camera')

if __name__=='__main__':
    try:main()
    except Exception:
        traceback.print_exc(file=sys.__stderr__);sys.__stderr__.flush();raise SystemExit(1)
