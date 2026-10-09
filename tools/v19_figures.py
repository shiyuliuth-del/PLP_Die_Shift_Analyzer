"""Reproducible light scientific figures from the frozen six-state audit."""
from pathlib import Path
import sys,json,pickle,math,argparse
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np,shapely,matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt,font_manager
from matplotlib.patches import Patch,PathPatch
from die_shift_studio.plot_geometry import geometry_path

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--gds',type=Path,required=True);parser.add_argument('--frames',type=Path,required=True);args=parser.parse_args()
 available={f.name for f in font_manager.fontManager.ttflist};font=next((s for s in ('Noto Sans CJK SC','Noto Sans CJK JP','Microsoft YaHei') if s in available),'DejaVu Sans')
 plt.rcParams.update({'font.family':font,'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'svg.fonttype':'none','axes.edgecolor':'#80909b','text.color':'#2e414e','axes.labelcolor':'#2e414e','xtick.color':'#52636f','ytick.color':'#52636f'})
 before=json.loads((ROOT/'results/baseline_interfaces_V19.json').read_text());after=json.loads((ROOT/'results/gd55_interfaces_V19.json').read_text());baseline={tuple(r['signs']):r for r in before['rows']}
 def save(fig,name):
  for ext in ('png','svg'):fig.savefig(ROOT/'figures'/(name+'.'+ext),dpi=300,bbox_inches='tight',facecolor='white')
  plt.close(fig)
 y=np.arange(6);old=[baseline[tuple(r['signs'])]['noncontact_missed_count'] for r in after['rows']];new=[r['noncontact_missed_count'] for r in after['rows']]
 fig,ax=plt.subplots(figsize=(9.2,4),layout='constrained');ax.barh(y+.16,old,.28,color='#cbd8e4',label='V18');ax.barh(y-.16,new,.28,color='#669e9c',label='V19')
 ax.scatter(new,y-.16,s=32,marker='o',facecolor='white',edgecolor='#408789',zorder=4,clip_on=False)
 for i,(a,b) in enumerate(zip(old,new)):
  ax.text(a+.5,i+.16,str(a),va='center',fontsize=10)
  ax.text(b+.7,i-.16,str(b),va='center',fontsize=10,color='#28787b')
 labels=[' / '.join(('+' if v>0 else '−')+str(n) for v,n in zip(r['signs'],[35,35,1])) for r in after['rows']]
 ax.set(yticks=y,yticklabels=labels,xlim=(0,52),xlabel='原始状态无接触、但未适配的接口数 / 个',ylabel='DX / DY / O（μm / μm / °）')
 ax.invert_yaxis();ax.grid(axis='x',color='#edf0f3');ax.set_axisbelow(True);ax.legend(frameon=False,ncol=2,loc='lower right');ax.set_title('GD55 · 固定六种极端状态的逐接口复核',loc='left',fontsize=13,pad=13)
 save(fig,'V19_interface_comparison')
 from die_shift_studio.core import read_gds,build_case,Parameters
 from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
 from die_shift_studio.settings import ViewSettings
 from tools.validate_gd55_v14 import ROLES,ORDER
 d=read_gds(args.gds);case=build_case(d,ROLES,layer_order=ORDER);e=PreviewEngine(GeometryCache(d),case,ROLES,{},Parameters(multi_bound_x_um=35,multi_bound_y_um=35),{},case['connections']);e.configure_deformation(True,ViewSettings())
 choices=[('pmm',24,89,'近端孔盘与邻近控制区'),('mmm',44,None,'同一铜区的接口独立适配'),('mpm',93,116,'相邻孔盘与量化轮廓修整')]
 fig,axes=plt.subplots(2,3,figsize=(10.5,7),layout='constrained')
 for col,(pose,index,via,title) in enumerate(choices):
  frame=pickle.load(open(args.frames/(pose+'.pkl'),'rb'));z=frame['states'];out=frame['deformation'];co=next(c for c in e.deformer.components if index in c.indices)
  replacement=next(v for v in out['replacements']['5/0'] if index in v['indices']);g=replacement['geometry']
  if via is None:
   missed=baseline[tuple(1 if s=='p' else -1 for s in pose)]['noncontact_missed'];via=next(r['via_index'] for r in missed if index in r['indices'])
  r=next(r for r in co.records if r['index']==via);moved=e.contact.moved(r['poly'],r['owner'],z);center=(np.asarray(r['center'])+np.asarray(moved.centroid.coords[0]))/2
  width=235.;height=235.;x0,y0=center-[width/2,height/2];x1,y1=center+[width/2,height/2]
  for row,actual in enumerate((co.poly,g)):
   ax=axes[row,col]
   for j,other in enumerate(e.contact.cache.layers['5/0']['geometry']):
    if j not in co.indices and other.intersects(shapely.box(x0,y0,x1,y1)):ax.add_patch(PathPatch(geometry_path(other),fc='#e0e8f1',ec='none'))
   ax.add_patch(PathPatch(geometry_path(actual),fc='#d6e1ec' if row==0 else '#9cafbf',ec='#8194a3' if row else 'none',lw=.4))
   for anchor in co.anchors:ax.add_patch(PathPatch(geometry_path(anchor),fc='#968bab',ec='none',alpha=.9))
   for record in co.records:
    v=e.contact.moved(record['poly'],record['owner'],z)
    if not v.intersects(shapely.box(x0-30,y0-30,x1+30,y1+30)):continue
    ax.add_patch(PathPatch(geometry_path(v),fc='#317784',ec='none'))
    for amount,color in [(15,'#67aaa1'),(30,'#b388bb')]:ax.add_patch(PathPatch(geometry_path(v.buffer(amount)),fc='none',ec=color,lw=.8,ls=(0,(4,3))))
    end=np.asarray(v.centroid.coords[0]);ax.plot(*np.array([record['center'],end]).T,color='#c88f42',lw=.9,ls='--')
   ax.set(xlim=(x0,x1),ylim=(y0,y1),aspect='equal',xlabel='X / μm',ylabel='Y / μm');ax.tick_params(labelsize=8)
   ax.set_title(('V18 · 原图保留' if row==0 else 'V19 · 已适配')+'\n'+title,fontsize=10)
 fig.legend(handles=[Patch(color='#9cafbf',label='通过检查的型变'),Patch(color='#317784',label='运动过孔'),Patch(color='#968bab',label='固定过孔')],loc='lower center',bbox_to_anchor=(.5,-.04),ncol=3,frameon=False)
 save(fig,'V19_GD55_details');e.close()
if __name__=='__main__':main()
