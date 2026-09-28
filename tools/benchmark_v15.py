"""Same GD55 geometry/state/rules as the saved V14 baseline, with V15 search."""
from pathlib import Path
import sys,json,argparse
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from die_shift_studio.settings import ViewSettings
from tools.validate_gd55_v14 import ROLES,ORDER
from tools.benchmark_v13 import run

def main():
    p=argparse.ArgumentParser();p.add_argument('--gds',type=Path,required=True);p.add_argument('--mode',choices=['auto','serial','thread','process'],default='auto');a=p.parse_args()
    q=dict(roles=ROLES,layer_order=ORDER,parameters=dict(line_width_um=20,line_space_um=20,annulus_um=15,multi_bound_x_um=25,multi_bound_y_um=25,bound_angle_deg=1))
    r=run(a.gds,q,np.tile([-25.,25.,np.pi/180],(3,1)),True,'V15_'+a.mode,ViewSettings(deformation_execution=a.mode))
    r.update(version='V15',hardware=__import__('die_shift_studio.resources',fromlist=['resource_profile']).resource_profile(),timing_scope='Local CPU; first solve vs in-session cache, excluding drawing')
    (ROOT/'results'/('performance_'+a.mode+'_V15.json')).write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n')
    print('V15_BENCHMARK_PASS', {k:r[k] for k in ('cold_frame_ms','accepted','unadapted','three_part_count','execution','workers')},flush=True)
if __name__=='__main__':main()
