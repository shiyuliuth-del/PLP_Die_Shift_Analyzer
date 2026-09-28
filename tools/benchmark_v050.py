"""Measure the actual new geometry/contact/probability pipeline; no GUI timings."""
from pathlib import Path
import json,sys,time,statistics,platform
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.recognition import automatic_parameters
from die_shift_studio.fast_preview import GeometryCache
from die_shift_studio.stack import Connections
from die_shift_studio.contact import ContactModel
from die_shift_studio.probability_overlay import probability_maps
from die_shift_studio.resources import resource_profile
ROOT=Path(__file__).resolve().parents[1];records={}
def measure(name,fn,repeats=1):
    times=[]
    for _ in range(repeats):
        start=time.perf_counter();value=fn();times.append(1000*(time.perf_counter()-start))
    records[name]={'samples_ms':times,'median_ms':statistics.median(times)}
    return value
data=measure('read_gds',lambda:read_gds(ROOT/'examples/示例C_多芯片晶圆接口.gds'))
roles=data.auto_roles;case=build_case(data,roles)
p=Parameters.from_dict(dict(interdie='present',**automatic_parameters(data,roles)[0]))
cache=measure('geometry_cache',lambda:GeometryCache(data))
con=measure('nominal_connections',lambda:Connections(data,roles,geometries=cache.geometries))
model=measure('geometric_networks',lambda:ContactModel(data,case,roles,con,cache))
measure('finite_contact_screen',lambda:model.screen(p),3)
states=np.array([[120.,10.,np.deg2rad(1)],[0.,0.,0.]])
measure('current_contacts_and_regions',lambda:model.current(states,p),7)
measure('probability_grid_4096',lambda:probability_maps(cache,model.bound,roles,p),3)
result={'version':'0.5.0','platform':platform.platform(),'resource_profile':resource_profile(),
        'geometry_count':data.count,'vertices':sum(len(a) for arrays in data.layers.values() for a in arrays),
        'scope':'Synthetic example C; wall time of computation only, excludes Qt/Matplotlib rendering. Shared host, not an isolated performance lab.',
        'timings':records}
(ROOT/'results/performance_v050.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({k:round(v['median_ms'],3) for k,v in records.items()},ensure_ascii=False))
