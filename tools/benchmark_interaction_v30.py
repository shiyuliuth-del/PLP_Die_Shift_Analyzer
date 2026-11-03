"""Measure completed camera, pick and cached-pose redraws on identical GD55 frames."""
import argparse,hashlib,json,os,sys,tempfile,time
from pathlib import Path
a=argparse.ArgumentParser();a.add_argument('--checkout',type=Path,required=True);a.add_argument('--gds',type=Path,required=True);a.add_argument('--out',type=Path,required=True)
args=a.parse_args();sys.path.insert(0,str(args.checkout.resolve()));os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import numpy as np
from dataclasses import asdict
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.stack import Connections
from die_shift_studio.gui import MainWindow,W
from die_shift_studio import __version__

def main():
    d=read_gds(args.gds);p=Parameters();roles={k:{'127/0':'封装边框','128/0':'芯片边框','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'}.get(k,'不参与') for k in d.layers}
    order=['6/0','5/0','4/0','3/0'];c=GeometryCache(d);con=Connections(d,roles,order,c.geometries);case=build_case(d,roles,connections=con)
    engine=PreviewEngine(c,case,roles,{},p,None,con);z=np.zeros((len(case['dies']),3));z[:,:2]=[35,-35];z[:,2]=np.deg2rad(-1.)
    frames=[engine.frame(z),engine.frame(-z)]
    app=W.QApplication([]);temp=tempfile.TemporaryDirectory();w=MainWindow(jobs_dir=Path(temp.name)/'Jobs')
    w.resize(1440,900);w.show();app.processEvents();w.set_data(d,dict(roles=roles,parameters=asdict(p),layer_order=order,schema_version=5))
    w.case=case;w.engine=engine;w.geometry_cache=c;w.current_frame=frames[0];w.plot_layout();app.processEvents()
    cx,cy=case['board'].centroid.coords[0];width=np.ptp(w.ax.get_xlim())*.5;height=np.ptp(w.ax.get_ylim())*.5
    samples={};facts=[];geometry=hashlib.sha256()
    def time_it(key,fn):
        start=time.perf_counter();fn();app.processEvents();samples.setdefault(key,[]).append(1000*(time.perf_counter()-start))
    def camera(i):
        f=1.+.025*np.cos(i*.36)
        w.set_view((cx+width*i/180.,cy+height*i/300.),width*f,height*f)
        w.viewport_timer.stop();w.viewport_changed(force=True)
    def signature():
        counts=[]
        for artist in w.zone_artists:
            paths=artist.get_segments();counts.append(len(paths))
            for path in paths:geometry.update(np.ascontiguousarray(path,dtype='<f8').tobytes())
        geometry.update(np.ascontiguousarray(w.connector_segments,dtype='<f8').tobytes())
        geometry.update(np.asarray([item['point'] for item in w.rendered_issues],dtype='<f8').tobytes())
        return counts+[len(w.rendered_issues),len(w.current_frame['contact']['issues']),len(w.connector_segments)]
    for i in range(20):
        time_it('pan_zoom',lambda i=i:camera(i));facts.append(signature())
    for i in range(20):
        ref=('5/0',i%6)
        time_it('select_clear',lambda ref=ref:(w.selection.apply_hits([ref],'replace'),w.selection.clear()))
    for i in range(16):
        def show():
            w.current_frame=frames[i%2];w.plot_layout(preserve=True)
        time_it('cached_pose_draw',show);facts.append(signature())
    stats={k:{'median_ms':float(np.median(v)),'p95_ms':float(np.percentile(v,95)),'samples':len(v)} for k,v in samples.items()}
    out=dict(version=__version__,gds_sha=d.sha256,shapes=sum(map(len,d.layers.values())),logical_cpus=os.cpu_count(),bounds_um=[35,35],angle_deg=1,process_um=[15,15,15],surface='1440 x 900 Linux Qt offscreen',timings=stats,display_counts_sha256=hashlib.sha256(json.dumps(facts).encode()).hexdigest(),drawn_coordinates_sha256=geometry.hexdigest(),first_view_counts=facts[0])
    args.out.write_text(json.dumps(out,indent=2)+'\n');w.close();app.processEvents();temp.cleanup()
if __name__=='__main__':main()
