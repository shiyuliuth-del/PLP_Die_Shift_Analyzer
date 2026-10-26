"""Static, reproducible V21 figures from synthetic GDS and actual preview data."""
from pathlib import Path
import sys,json,numpy as np,shapely,matplotlib
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));matplotlib.use('Agg')
from matplotlib import pyplot as plt,font_manager
from matplotlib.patches import PathPatch,Patch
from die_shift_studio.plot_geometry import geometry_path
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.width_analysis import COLORS

def fixture(name):
 q=json.loads((ROOT/'examples'/(name+'.json')).read_text(encoding='utf-8'));d=read_gds(ROOT/'examples'/q['gds_path']);c=build_case(d,q['roles'],layer_order=q['layer_order']);p=Parameters.from_dict(q['parameters']);return d,c,PreviewEngine(GeometryCache(d),c,q['roles'],{},p,{},c['connections'])

def main():
 fonts={f.name for f in font_manager.fontManager.ttflist};font=next((f for f in ('Noto Sans CJK SC','Noto Sans CJK JP','Microsoft YaHei') if f in fonts),'DejaVu Sans')
 plt.rcParams.update({'font.family':font,'font.size':10,'svg.fonttype':'none','axes.spines.top':False,'axes.spines.right':False,'axes.edgecolor':'#82919a','text.color':'#304552','axes.labelcolor':'#304552','xtick.color':'#526773','ytick.color':'#526773','axes.unicode_minus':False})
 def save(fig,name):
  (ROOT/'figures').mkdir(exist_ok=True)
  for ext in ('png','svg'):fig.savefig(ROOT/'figures'/f'{name}.{ext}',dpi=260,bbox_inches='tight',facecolor='white')
  plt.close(fig)
 d,c,e=fixture('示例M_不同芯片底层与Pad随动');z=np.array([[35,-35,-np.pi/180],[-35,35,np.pi/180]]);f=e.frame(z)
 fig,ax=plt.subplots(figsize=(10,5.5),layout='constrained')
 colors=['#78aaa2','#899dc2']
 for i,die in enumerate(c['dies']):
  ax.add_patch(PathPatch(geometry_path(die),fc=colors[i],ec=colors[i],alpha=.17,lw=1.2));ax.text(die.centroid.x,die.centroid.y-160,f'DIE {i+1}\n起始层：'+('2/0' if i==0 else '3/0'),ha='center',va='center',fontsize=11)
 for key in ('3/0','4/0'):
  for i,(old,new) in enumerate(zip(d.layers[key],f['layers'][key]['polygons'])):
   ax.add_patch(PathPatch(geometry_path(shapely.Polygon(old)),fc='none',ec='#acb9c5',ls='--',lw=.8))
   ax.add_patch(PathPatch(geometry_path(shapely.Polygon(new)),fc=colors[i],ec=colors[i],lw=.8))
 for i in range(2):
  origin=np.asarray(shapely.Polygon(d.layers['4/0'][i]).centroid.coords[0]);end=f['layers']['4/0']['centers'][i]
  ax.annotate(f'外部 Pad → DIE {i+1}',xy=end,xytext=(origin[0]-350,origin[1]+220),arrowprops=dict(arrowstyle='->',color='#8e9da8',lw=.9),fontsize=10)
 ax.set(xlim=(-1600,1800),ylim=(-550,1000),aspect='equal',xlabel='X / μm',ylabel='Y / μm',title='不同芯片可从不同金属层开始；外部 Pad 沿连接归属随动')
 ax.grid(color='#edf1f4',lw=.6);ax.set_axisbelow(True);ax.legend(handles=[Patch(fc='none',ec='#acb9c5',ls='--',label='名义图形'),Patch(fc='#78aaa2',label='DIE 1：+35 / −35 μm，−1°'),Patch(fc='#899dc2',label='DIE 2：−35 / +35 μm，+1°')],loc='lower center',bbox_to_anchor=(.5,-.22),ncol=3,frameon=False,fontsize=9)
 save(fig,'V21_local_stack_motion');e.close()
 d,c,e=fixture('示例N_三线路窄通道宽度');e.configure_width(True,ViewSettings());fig,axes=plt.subplots(1,3,figsize=(11,4.4),layout='constrained');values=[]
 for ax,(dx,deform,title) in zip(axes,[(0,False,'a  名义位置'),(35,False,'b  Pad 移入通道'),(-35,True,'c  实际预演型变后')]):
  e.configure_deformation(deform,ViewSettings());z=np.array([[dx,0.,0.],[0.,0.,0.]]);f=e.frame(z);replaced={}
  for item in f.get('deformation',{}).get('replacements',{}).get('5/0',[]):
   for i in item['indices']:replaced[i]=item['geometry']
  for i,g in enumerate(d.layers['5/0']):ax.add_patch(PathPatch(geometry_path(replaced.get(i,shapely.Polygon(g))),fc='#a1b3c2' if i in replaced else '#d9e4ee',ec='none'))
  for v in f['layers']['4/0']['polygons']:
   g=shapely.Polygon(v);ax.add_patch(PathPatch(geometry_path(g),fc='#397c85',ec='none'));ax.add_patch(PathPatch(geometry_path(g.buffer(15)),fc='none',ec='#6faa9e',lw=.8,ls=(0,(4,3))))
  q=next(r for r in f['width_analysis']['rows'] if r['plane']=='5' and set(r['pair'])=={'pad:4/0:0','pad:4/0:1'});values.append({k:q[k] for k in ('gap_um','strands','capacity','status')})
  color=COLORS[q['status']];ax.add_patch(PathPatch(geometry_path(q['rectangle']),fc=color,alpha=.2,ec=color,lw=1.2))
  if q['status']!='充足':
   for k,i in q['refs']:ax.add_patch(PathPatch(geometry_path(shapely.Polygon(f['layers'][k]['polygons'][i])),fc='none',ec=color,lw=1.1))
  x,y=q['point'];ax.annotate('',xy=(x-q['gap_um']/2,145),xytext=(x+q['gap_um']/2,145),arrowprops=dict(arrowstyle='<->',color='#4e6470',lw=.9))
  ax.text(x,163,f"g = {q['gap_um']:.2f} μm",ha='center',fontsize=10)
  ax.set(xlim=(-210,210),ylim=(-175,205),aspect='equal',xlabel='X / μm',ylabel='Y / μm',title=title+'\n'+f"需求 3 条 · 可容 {q['capacity']} 条 · {q['status']}")
  ax.grid(color='#edf1f4',lw=.5);ax.set_axisbelow(True);ax.tick_params(labelsize=8)
 fig.supxlabel('示例 N 局部放大 · w = s = 15 μm；所需通道宽度 = 3×15 + 4×15 = 105 μm',fontsize=11)
 save(fig,'V21_channel_capacity');e.close()
 (ROOT/'results/figures_V21.json').write_text(json.dumps(dict(source='examples/示例N_三线路窄通道宽度.gds',channels=values,scope='Local width capacity; no claim of complete DRC or global routability.'),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
if __name__=='__main__':main()
