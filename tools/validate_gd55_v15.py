"""GD55 20/20/15 μm regression and continuous safe-range evidence, read only."""
import argparse,hashlib,itertools,json,math,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import shapely
from die_shift_studio.core import read_gds,build_case,Parameters,evaluate,clean_json
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.deformation import DeformationPreview
from die_shift_studio.settings import ViewSettings
from die_shift_studio.safe_ranges import BoxCertificate
from die_shift_studio.drc import rule_region
from tools.validate_gd55_v14 import ROLES,ORDER


def detail(path):
    d=read_gds(path);c=build_case(d,ROLES,layer_order=ORDER)
    p=Parameters(multi_bound_x_um=25,multi_bound_y_um=25,bound_angle_deg=1,line_width_um=20,line_space_um=20,annulus_um=15)
    e=PreviewEngine(GeometryCache(d),c,ROLES,{},p,{},c['connections']);dp=DeformationPreview(e.contact,p,{},ViewSettings(deformation_execution='serial'))
    co=next(q for q in dp.components if q.key=='5/0' and q.indices==(93,));rows=[];rendered=None
    for signs in itertools.product((-1,1),repeat=3):
        z=np.tile(np.array([25,25,math.pi/180])*signs,(3,1));vias=dp.prepare_pose(z);start=time.perf_counter()
        try:
            candidate,field,span=dp.search(co,co.records,co.anchors,z,vias,dp._snapshot({}))
            verified=field.verified;reason=field.failure_reasons
            if verified:
                assert not dp.candidate_violations(co,candidate,co.records,co.anchors,z,vias,dp._snapshot({}))
                assert rule_region(candidate,20,d.dbu_um).is_empty
                if signs==(-1,1,1):rendered=(candidate,field,z,span)
        except (ValueError,RuntimeError) as exc:verified=False;reason=[str(exc)];span=None
        rows.append(dict(signs=signs,verified=verified,reason=reason,span_um=span,seconds=time.perf_counter()-start))
    assert rendered is not None,'User red-circle state must adapt with annulus 15'
    draw_detail(e,co,*rendered)
    out=dict(status='PASS',source_sha256=d.sha256,parameters=p.__dict__,source_layer='5/0',source_index=93,via_indices=[r['index'] for r in co.records],states=rows,
             interpretation='Each valid local result independently checked against original neighbours; whole-layout benchmark performs final simultaneous acceptance.')
    dp.close();e.close();return out


def draw_detail(e,c,g,field,z,span):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import PathPatch
    from die_shift_studio.plot_geometry import geometry_path
    from matplotlib import font_manager
    font=next((x for x in ('Noto Sans CJK SC','Noto Sans CJK JP','Microsoft YaHei') if x in {f.name for f in font_manager.fontManager.ttflist}),'DejaVu Sans')
    plt.rcParams.update({'font.family':font,'font.size':10,'svg.fonttype':'none','axes.spines.top':False,'axes.spines.right':False})
    fig,axes=plt.subplots(1,2,figsize=(9,4.4),layout='constrained')
    from shapely.geometry import Point
    support=getattr(field,'support',None)
    if support is None:support=shapely.union_all([Point(r['center']).buffer(inner+span) for r,inner in zip(field.records,field.inner)])
    dark=g.difference(c.poly.difference(support))
    for ax,after in zip(axes,(False,True)):
        for j,poly in enumerate(e.contact.cache.layers['5/0']['geometry']):
            current=g if after and j==93 else poly
            ax.add_patch(PathPatch(geometry_path(current),facecolor='#d9e5ef',edgecolor='none'))
            if after and j==93:ax.add_patch(PathPatch(geometry_path(dark),facecolor='#8eafc7',edgecolor='none'))
        for r in c.records:
            via=e.contact.moved(r['poly'],r['owner'],z) if after else r['poly']
            ax.add_patch(PathPatch(geometry_path(via),facecolor='#407989',edgecolor='none'))
            for radius,color in [(15,'#549d99'),(35,'#a280ae')]:
                ax.add_patch(PathPatch(geometry_path(via.buffer(radius)),fill=False,edgecolor=color,ls=(0,(4,3)),lw=.85))
        ax.set(xlim=(-610,-385),ylim=(-980,-735),aspect='equal',xlabel='X / μm',ylabel='Y / μm',title='a  原孔盘' if not after else 'b  通过型变校验（包围15 μm）')
    for ext in ('png','svg'):fig.savefig(ROOT/'figures'/('V15_GD55_detail.'+ext),dpi=300)
    plt.close(fig)


def assessment(path):
    d=read_gds(path);p=Parameters(multi_bound_x_um=25,multi_bound_y_um=25,bound_angle_deg=1,line_width_um=20,line_space_um=20,annulus_um=15)
    start=time.perf_counter();r=evaluate(d,ROLES,p,layer_order=ORDER,progress=lambda n,s:print(n,s,flush=True));r['seconds']=time.perf_counter()-start
    c=build_case(d,ROLES,layer_order=ORDER);e=PreviewEngine(GeometryCache(d),c,ROLES,{},p,{},c['connections'])
    # Recreate independent certificates and also spot-check actual contacts.
    random=np.random.default_rng(1501)
    bounds=[]
    for row in r['safe_ranges']['rows']:
        assert row['certified'];lo=np.array(row['minimum']);hi=np.array(row['maximum']);owner=row['die']-1
        assert BoxCertificate(e.contact,owner,p,2048).check(lo,hi)=='safe'
        bounds.append((lo,hi))
    for _ in range(500):
        states=np.array([random.uniform(lo,hi) for lo,hi in bounds])
        assert not e.contact.current(states,p)['issues']
    r['safe_range_independent_audit']=dict(status='PASS',independent_certificates=3,additional_random_combinations=500,source_unchanged=True)
    e.close();return r


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--gds',type=Path,required=True);parser.add_argument('--detail-only',action='store_true');args=parser.parse_args()
    before=hashlib.sha256(args.gds.read_bytes()).hexdigest()
    (ROOT/'results/detail_GD55_V15.json').write_text(json.dumps(clean_json(detail(args.gds)),ensure_ascii=False,indent=2)+'\n')
    if not args.detail_only:(ROOT/'results/assessment_GD55_V15.json').write_text(json.dumps(clean_json(assessment(args.gds)),ensure_ascii=False,indent=2)+'\n')
    assert hashlib.sha256(args.gds.read_bytes()).hexdigest()==before
    print('GD55_V15_PASS',flush=True)
if __name__=='__main__':main()
