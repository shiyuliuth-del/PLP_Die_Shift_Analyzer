"""Light scientific before/after figures from actual audited GD55 copper."""
from pathlib import Path
import sys,json,math,argparse
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np,shapely,matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt,font_manager
from matplotlib.patches import PathPatch,Patch
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.plot_geometry import geometry_path
from tools.validate_gd55_v14 import ROLES,ORDER
from die_shift_studio.settings import ViewSettings

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--gds',type=Path,required=True);ap.add_argument('--baseline-geometry',type=Path,required=True);args=ap.parse_args()
 font=next((x for x in ('Noto Sans CJK SC','Noto Sans CJK JP','Microsoft YaHei') if x in {f.name for f in font_manager.fontManager.ttflist}),'DejaVu Sans')
 plt.rcParams.update({'font.family':font,'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'svg.fonttype':'none','axes.edgecolor':'#7b8995'})
 old=json.loads(args.baseline_geometry.read_text());new=json.loads((ROOT/'results/gd55_sweep_V18.json.geometry.json').read_text())
 d=read_gds(args.gds);case=build_case(d,ROLES,layer_order=ORDER);e=PreviewEngine(GeometryCache(d),case,ROLES,{},Parameters(multi_bound_x_um=35,multi_bound_y_um=35),{},case['connections'])
 e.configure_deformation(True,ViewSettings());states=np.tile([-35.,-35.,-math.pi/180],(3,1))
 fig,axes=plt.subplots(2,3,figsize=(10.2,7),layout='constrained')
 titles=[['原型变：肩部凹陷','未适配，保留原图','未适配，保留原图'],['短孔盘：顺滑轮廓','长连线：局部弯曲','近端固定孔：光滑包络']]
 for col,index in enumerate((22,104,110)):
  co=next(c for c in e.deformer.components if c.indices==(index,));xy=shapely.get_coordinates(co.poly);lo=xy.min(axis=0)-25;hi=xy.max(axis=0)+25
  if index==104:lo[1]=hi[1]-290
  lo[0]-=70
  for row,mapping in enumerate((old,new)):
   ax=axes[row,col];g=shapely.from_wkt(mapping[str(index)]) if str(index) in mapping else co.poly
   ax.add_patch(PathPatch(geometry_path(co.poly),fc='#eff3f8',ec='#c3d0dd',lw=.6,ls='--'))
   ax.add_patch(PathPatch(geometry_path(g),fc='#9aaabd' if row==0 and str(index) in mapping else '#96bacd' if row==1 else '#d6e1ee',ec='none'))
   for fixed in co.anchors:ax.add_patch(PathPatch(geometry_path(fixed),fc='#8882a9',ec='none',alpha=.8))
   for r in co.records:
    via=e.contact.moved(r['poly'],r['owner'],states);ax.add_patch(PathPatch(geometry_path(via),fc='#2d7a80',ec='none'))
    for pad,color in ((15,'#5ca89d'),(30,'#a878bc')):ax.add_patch(PathPatch(geometry_path(via.buffer(pad)),fc='none',ec=color,ls=(0,(4,3)),lw=.8))
    center=np.asarray(via.centroid.coords[0]);ax.plot(*np.array([r['center'],center]).T,color='#d39142',ls='--',lw=.9)
   ax.set(xlim=(lo[0],hi[0]),ylim=(lo[1],hi[1]),aspect='equal',xlabel='X / μm',ylabel='Y / μm')
   ax.set_title(('V17 · ' if row==0 else 'V18 · ')+titles[row][col],fontsize=11);ax.tick_params(labelsize=8)
 fig.legend(handles=[Patch(color='#96bacd',label='通过检查的型变'),Patch(color='#2d7a80',label='运动过孔'),Patch(color='#8882a9',label='固定过孔')],loc='lower center',bbox_to_anchor=(.5,-.04),ncol=3,frameon=False)
 for ext in ('png','svg'):fig.savefig(ROOT/'figures'/('V18_GD55_details.'+ext),dpi=300,bbox_inches='tight')
 plt.close(fig);e.close()
 baseline=json.loads((ROOT/'results/baseline_sweep_V18.json').read_text());current=json.loads((ROOT/'results/gd55_sweep_V18.json').read_text())
 x=np.arange(len(current['rows']));a=[len(v['accepted']) for v in baseline['rows']];b=[len(v['accepted']) for v in current['rows']]
 fig,ax=plt.subplots(figsize=(10,3.6),layout='constrained');ax.bar(x-.18,a,.36,color='#cbd6e2',label='V17');ax.bar(x+.18,b,.36,color='#86b5ac',label='V18')
 for i,v in enumerate(b):ax.text(i+.18,v+1,str(v),ha='center',fontsize=9,color='#3e6e65')
 ax.set(xticks=x,xticklabels=['−−−','−−+','−+−','−++','+−−','+−+','++−','+++','混合1','混合2'][:len(x)],ylabel='通过适配的铜连通区 / 个',xlabel='X / Y / O 的符号；每项按 ±35 μm / ±35 μm / ±1°',ylim=(0,90))
 ax.legend(frameon=False,ncol=2,loc='upper right');ax.grid(axis='y',color='#edf0f3');ax.set_axisbelow(True)
 for ext in ('png','svg'):fig.savefig(ROOT/'figures'/('V18_extreme_comparison.'+ext),dpi=300,bbox_inches='tight')
 plt.close(fig)
if __name__=='__main__':main()
