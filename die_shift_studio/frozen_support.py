"""Writable Jobs beside the executable, bundled read-only sample bootstrap."""
from pathlib import Path
import json,os,sys,shutil,traceback
from .jobs import installation_root

def hide_owned_console():
    """Hide only a private Python launcher console, never the caller's terminal."""
    if sys.platform!='win32':return
    import ctypes
    kernel=ctypes.windll.kernel32
    members=(ctypes.c_uint32*2)()
    if kernel.GetConsoleProcessList(members,len(members))==1 and members[0]==os.getpid():
        kernel.GetConsoleWindow.restype=ctypes.c_void_p
        window=kernel.GetConsoleWindow()
        if window:ctypes.windll.user32.ShowWindow(ctypes.c_void_p(window),0)


def prepare_runtime():
    # Both pythonw and the windowed EXE can start without console streams.
    for name in ('stdout','stderr'):
        if getattr(sys,name,None) is None:setattr(sys,name,open(os.devnull,'w',encoding='utf-8'))
    if getattr(sys,'frozen',False):
        root=installation_root();seed=Path(getattr(sys,'_MEIPASS',root))/'seed'
        for folder in ('examples','Jobs'):
            (root/folder).mkdir(exist_ok=True)
            for p in (seed/folder).glob('*'):
                target=root/folder/p.name
                if p.is_file() and not target.exists():shutil.copy2(p,target)

def smoke(output):
    from .gui import MainWindow,W,QtCore
    from .core import read_gds,evaluate,Parameters
    from .recognition import automatic_parameters
    from .report import report_document
    from .formula_render import set_document
    from .fast_preview import GeometryCache,PreviewEngine
    from .stack import Connections
    from .preview import default_motion
    import numpy as np
    root=installation_root();app=W.QApplication.instance() or W.QApplication([]);window=MainWindow();window.show()
    record=dict(status='FAIL',frozen=bool(getattr(sys,'frozen',False)),platform=sys.platform)
    try:
        path=root/'examples/示例C_多芯片晶圆接口.gds';data=read_gds(path);project=json.loads(path.with_suffix('.json').read_text(encoding='utf-8'));order=project['layer_order'];roles=project['roles'];window.set_data(data,project)
        p=Parameters.from_dict(dict(interdie='present',**automatic_parameters(data,roles)[0]))
        result=evaluate(data,roles,p,layer_order=order);window.display_result(result)
        html,resources=report_document(result);set_document(window.detail,html,resources);app.processEvents();window.canvas.draw()
        cache=GeometryCache(data);con=Connections(data,roles,order);motions={k:default_motion(v) for k,v in roles.items()}
        engine=PreviewEngine(cache,window.case,roles,motions,p,result,con)
        frame=engine.frame(np.zeros((2,3)))
        from .space_map import space_maps
        from .probability_overlay import probability_maps
        window.engine=engine;window.geometry_cache=cache;window.current_frame=frame
        window.space_data=space_maps(engine.contact,p);window.space_toggle.setChecked(True)
        window.probability_data=probability_maps(cache,engine.bound,roles,p,masks=engine.contact.interface_masks)
        window.probability_toggle.setChecked(True);app.processEvents();window.canvas.draw()
        assert len(window.space_artists)==2 and len(frame['contact']['regions'])==64
        masses=[q['probability'] for q in window.probability_data['maps']]
        from .statistics import statistical_state
        np.testing.assert_allclose(window.probability_data['sample_standard_deviations'],np.sqrt(np.diag(statistical_state(p,2)[1])),rtol=.04,atol=1e-12,err_msg='Sobol runtime resource or normal sample distribution is invalid')
        assert masses and all(np.isfinite(a).all() and a.min()>=0 and a.max()<=1 and np.count_nonzero(a)>10 for a in masses),'Sobol probability grid is invalid or degenerate'
        assert all(abs(q['integrated_area_um2']/q['via_area_um2']-1)<.04 for q in window.probability_data['maps'])
        from .settings import ViewSettings
        from .contact import ContactModel
        external=read_gds(root/'examples/示例D_芯片外扇出.gds')
        from .core import build_case
        external_project=json.loads((root/'examples/示例D_芯片外扇出.json').read_text(encoding='utf-8'))
        external_case=build_case(external,external_project['roles'],layer_order=external_project['layer_order']);model=ContactModel(external,external_case,external_project['roles'],external_case['connections'])
        deterministic=probability_maps(model.cache,model.bound,model.roles,p,masks=model.interface_masks,settings=ViewSettings(probability_method='quadrature'))
        assert len(deterministic['maps'])==2 and deterministic['samples']==0
        assert model.current([[65,0,0],[0,0,0]],p)['intrusions']
        from .deformation import width_ok
        from shapely.geometry import box
        from .native_backend import backend
        assert width_ok(box(-25,-25,25,25),20,.001) and not width_ok(box(-5,-25,5,25),20,.001)
        trace_path=root/'examples/示例G_小偏移与三段追溯.gds'
        trace_project=json.loads(trace_path.with_suffix('.json').read_text(encoding='utf-8'))
        td=read_gds(trace_path);tc=build_case(td,trace_project['roles'],layer_order=trace_project['layer_order']);tp=Parameters.from_dict(trace_project['parameters'])
        te=PreviewEngine(GeometryCache(td),tc,trace_project['roles'],{},tp,{},tc['connections'])
        te.configure_deformation(True,ViewSettings());te.configure_drc(True,ViewSettings())
        pose=np.array([[1.,1,0],[0,35,0]]);tf=te.frame(pose)
        assert tf['deformation']['accepted_count']==2 and not tf['drc']
        te.configure_deformation(False,ViewSettings());te.frame(pose);te.configure_deformation(True,ViewSettings())
        assert te.frame(pose)['deformation']['accepted_count']==2
        record.update(deformation_routes=2,configuration_retry=True)
        te.close()
        record.update(native_drc=backend().info,status='PASS',version=result['version'],jobs=str(window.jobs_dir),formulas=len(resources),regions=len(frame['contact']['regions']),space_maps=len(window.space_artists),probability_maps=len(masses),sample_standard_deviations=window.probability_data['sample_standard_deviations'],min_occupied_probability_cells=min(int(np.count_nonzero(a)) for a in masses))
    except Exception:record['error']=traceback.format_exc()
    finally:
        import time
        window.close();deadline=time.monotonic()+30
        while window._tasks and time.monotonic()<deadline:app.processEvents();time.sleep(.005)
        if window._tasks:record.update(status='FAIL',shutdown_error='Background workers did not stop within 30 seconds')
        app.processEvents();Path(output).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if record['status']=='PASS' else 1
