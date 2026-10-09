"""Scientific figures from V20 material certificates and actual final layouts."""
from pathlib import Path
import argparse,json,sys,pickle
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np,shapely,matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt,font_manager
from matplotlib.patches import PathPatch,Patch
from die_shift_studio.plot_geometry import geometry_path

def run(gds,frames):
 fonts={f.name for f in font_manager.fontManager.ttflist};font=next((s for s in ('Noto Sans CJK SC','Noto Sans CJK JP','Microsoft YaHei') if s in fonts),'DejaVu Sans')
 plt.rcParams.update({'font.family':font,'font.size':10,'svg.fonttype':'none','axes.spines.top':False,'axes.spines.right':False,'axes.edgecolor':'#83929c','text.color':'#304552','axes.labelcolor':'#304552'})
 (ROOT/'figures').mkdir(exist_ok=True)
 def save(fig,name):
  for ext in ('png','svg'):fig.savefig(ROOT/'figures'/(name+'.'+ext),dpi=300,bbox_inches='tight',facecolor='white')
  plt.close(fig)
 audit=json.loads((ROOT/'results/gd55_material_V20.json').read_text());rows=audit['rows']
 fig,ax=plt.subplots(figsize=(9.5,3.8),layout='constrained');y=np.arange(len(rows));ok=np.array([r['noncontact_count']-r['noncontact_missed_count'] for r in rows]);miss=np.array([r['noncontact_missed_count'] for r in rows])
 ax.barh(y,ok,.5,color='#95bdb4',label='原材料随动与最终检查均通过');ax.barh(y,miss,.5,left=ok,color='#eed6ac',label='未解，保留原图')
 for i,(a,b) in enumerate(zip(ok,miss)):
  ax.text(a/2,i,str(a),ha='center',va='center',color='#285d55');ax.text(a+b/2,i,str(b),ha='center',va='center',color='#795e31')
 labels=[' / '.join(('+' if v>0 else '−')+str(n) for v,n in zip(r['signs'],(35,35,1))) for r in rows]
 ax.set(yticks=y,yticklabels=labels,xlabel='原始状态无接触的接口 / 个',ylabel='DX / DY / O（μm / μm / °）');ax.invert_yaxis();ax.grid(axis='x',color='#edf1f4');ax.set_axisbelow(True);ax.legend(frameon=False,loc='upper center',bbox_to_anchor=(.5,-.18),ncol=2);ax.set_title('GD55 · 六种极端状态的材料随动验收',loc='left',pad=12)
 save(fig,'V20_material_outcomes')
 from die_shift_studio.core import read_gds,build_case,Parameters
 from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
 from die_shift_studio.settings import ViewSettings
 from tools.validate_gd55_v14 import ROLES,ORDER
 d=read_gds(gds);c=build_case(d,ROLES,layer_order=ORDER);e=PreviewEngine(GeometryCache(d),c,ROLES,{},Parameters(multi_bound_x_um=35,multi_bound_y_um=35),{},c['connections']);e.configure_deformation(True,ViewSettings())
 fig,axes=plt.subplots(1,3,figsize=(10.5,3.8),layout='constrained')
 choices=[('pmm',22,'原孔盘材料随动'),('mmm',93,'同网孔盘与连接段'),('pmm',67,'未通过时保留源铜')]
 try:
  for ax,(pose,index,title) in zip(axes,choices):
   f=pickle.load(open(frames/(pose+'.pkl'),'rb'));out=f['deformation'];co=next(v for v in e.deformer.components if index in v.indices);v=next((v for v in out['replacements'].get('5/0',[]) if index in v['indices']),None);g=co.poly if v is None else v['geometry']
   record=next((r for r in co.records if v and (r['key'],r['index']) in set(map(tuple,v['adapted_vias']))),co.records[0]);moved=e.contact.moved(record['poly'],record['owner'],f['states']);center=(np.array(record['center'])+np.array(moved.centroid.coords[0]))/2
   region=shapely.box(*(center-125),*(center+125))
   ids,geometries,tree=e.deformer._snapshot(out['replacements'])['5']
   for j in tree.query(region):
    if e.contact.nodes[ids[j]]['net']!=co.net:ax.add_patch(PathPatch(geometry_path(geometries[j]),fc='#e5ebf2',ec='none'))
   ax.add_patch(PathPatch(geometry_path(g),fc='#9eafbe' if v is not None else '#d6e1ee',ec='none'))
   ax.add_patch(PathPatch(geometry_path(co.poly),fc='none',ec='#8599ab',ls='--',lw=.7))
   for a in co.anchors:ax.add_patch(PathPatch(geometry_path(a),fc='#9a8daf',ec='none'))
   ax.add_patch(PathPatch(geometry_path(moved),fc='#347d83',ec='none'))
   for amount,color in ((15,'#71ada7'),(30,'#b78abb')):ax.add_patch(PathPatch(geometry_path(moved.buffer(amount)),fc='none',ec=color,lw=.8,ls='--'))
   if v is not None:
    for sample in v['material_certificate']['samples'][:1]:
     xy=np.asarray(sample['source']);to=np.asarray(sample['mapped']);keep=np.unique(np.linspace(0,len(xy)-1,min(3,len(xy)),dtype=int))
     ax.scatter(*xy[keep].T,s=14,facecolors='white',edgecolors='#b87835',zorder=8)
     ax.scatter(*to[keep].T,s=14,color='#b87835',zorder=8)
     for a,b in zip(xy[keep],to[keep]):ax.annotate('',xy=b,xytext=a,arrowprops=dict(arrowstyle='->',color='#b87835',lw=.7),zorder=8)
   else:ax.plot(*moved.centroid.coords[0],marker='x',color='#b57830',ms=7,mew=1.5)
   ax.set(xlim=(center[0]-125,center[0]+125),ylim=(center[1]-125,center[1]+125),aspect='equal',xlabel='X / μm',ylabel='Y / μm');ax.tick_params(labelsize=8)
   ax.set_title(title+'\n'+('通过' if v else '未解')+f' · 5/0 #{index}',fontsize=11)
  fig.legend(handles=[Patch(color='#9eafbe',label='型变铜'),Patch(color='#347d83',label='运动孔'),Patch(color='#9a8daf',label='固定孔')],loc='lower center',bbox_to_anchor=(.5,-.08),ncol=3,frameon=False)
  save(fig,'V20_GD55_material_detail')
 finally:e.close()
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--gds',type=Path,required=True);p.add_argument('--frames',type=Path,required=True);a=p.parse_args();run(a.gds,a.frames)
