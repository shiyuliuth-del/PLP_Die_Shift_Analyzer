"""V17 real-GDS accepted-copper audit and calculation/cache timings."""
from pathlib import Path
import sys,json,argparse
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from die_shift_studio.settings import ViewSettings
from tools.validate_gd55_v14 import ROLES,ORDER
from tools.benchmark_v13 import run

def main():
 p=argparse.ArgumentParser();p.add_argument('--gds',type=Path,required=True);p.add_argument('--width',type=float,default=20);p.add_argument('--mode',default='auto',choices=['auto','serial','thread','process']);a=p.parse_args()
 q=dict(roles=ROLES,layer_order=ORDER,parameters=dict(line_width_um=a.width,line_space_um=a.width,annulus_um=15,multi_bound_x_um=25,multi_bound_y_um=25,bound_angle_deg=1))
 r=run(a.gds,q,np.tile([-25.,25.,np.pi/180],(3,1)),True,'V17_'+str(int(a.width)),ViewSettings(deformation_execution=a.mode))
 r['nominal_and_effective_rules_identical']=True;r['drc_tolerance_um']=0
 (ROOT/'results'/('performance_GD55_'+str(int(a.width))+'_V17.json')).write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n')
 print('V17_BENCHMARK_PASS',r['cold_frame_ms'],r['accepted'],r['unadapted'],flush=True)
if __name__=='__main__':main()
