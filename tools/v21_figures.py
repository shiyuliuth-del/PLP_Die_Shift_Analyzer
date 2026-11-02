"""Static, reproducible V21 figures from synthetic GDS and actual preview data."""
from pathlib import Path
import sys,json,numpy as np,shapely,matplotlib
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));matplotlib.use('Agg')
from matplotlib import pyplot as plt,font_manager
from matplotlib.patches import PathPatch,Patch
from matplotlib.lines import Line2D
from die_shift_studio.plot_geometry import geometry_path
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.width_analysis import COLORS,gate_rectangle

def fixture(name):
 q=json.loads((ROOT/'examples'/(name+'.json')).read_text(encoding='utf-8'));d=read_gds(ROOT/'examples'/q['gds_path']);c=build_case(d,q['roles'],layer_order=q['layer_order']);p=Parameters.from_dict(q['parameters']);return d,c,PreviewEngine(GeometryCache(d),c,q['roles'],{},p,{},c['connections'])

def channel_figure(save):
 d,c,e=fixture('示例O_运动网络三线路通道')
 e.configure_width(True,ViewSettings())
 fig,axes=plt.subplots(1,3,figsize=(9,3.6),layout='constrained')
 values=[]
 for ax,dx in zip(axes,[0,3,35]):
  f=e.frame(np.array([[dx,0.,0.],[0.,0.,0.]]))
  analysis=f['width_analysis']
  for o in analysis['retained']:
   ax.add_patch(PathPatch(geometry_path(o['geometry']),fc='#dbe6eb',ec='#78909c',lw=.8))
  r=min((row for row in analysis['rows'] if row['pair']==('运动过孔孔盘 4/0 #1','运动过孔孔盘 4/0 #2')),key=lambda q:abs(q['point'][1]))
  for key,i in r['refs']:
   ax.add_patch(PathPatch(geometry_path(shapely.Polygon(d.layers[key][i])),fc='#eff3f5',ec=COLORS[r['status']],lw=1.0))
  box=gate_rectangle(r,15,15)
  ax.add_patch(PathPatch(geometry_path(box),fc=COLORS[r['status']],alpha=.22))
  ax.add_patch(PathPatch(geometry_path(box),fc='none',ec=COLORS[r['status']],lw=1.4))
  ax.annotate(f"{r['gap_um']:g} μm",xy=r['point'],xytext=(r['point'][0]+11,70),color='#414f59',fontsize=9,
              arrowprops=dict(arrowstyle='->',lw=.8,color='#526777'),bbox=dict(fc='white',ec='none',alpha=.9,pad=1))
  ax.set(xlim=(-163,163),ylim=(-145,145),aspect='equal',xlabel='X / μm',
         title=f"DX {dx:+g} μm  ·  {r['status']}\n原可通 {r['original']}　必须 {r['required']}　现可通 {r['current']}")
  ax.grid(color='#ecf1f3',linewidth=.5);ax.set_axisbelow(True)
  values.append({k:r[k] for k in ('original','required','current','gap_um','status')})
 axes[0].set_ylabel('Y / μm')
 save(fig,'V23_channel_classes')
 e.close()
 return values


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
 values=channel_figure(save)
 d,c,e=fixture('示例F_逐芯片幅度与迭代窗口');e.configure_reconstruction(True,ViewSettings());f=e.frame(np.array([[35,0.,0.],[0.,0.,0.]]))
 piece=next(iter(f['reconstruction']['pieces']))
 fig,ax=plt.subplots(figsize=(6.6,5.6),layout='constrained')
 for g in d.layers['5/0']:ax.add_patch(PathPatch(geometry_path(shapely.Polygon(g)),fc='none',ec='#acb9c5',ls='--',lw=.8))
 for g in f['layers']['5/0']['polygons']:ax.add_patch(PathPatch(geometry_path(shapely.Polygon(g)),fc='#e7edf3',ec='none'))
 for v in f['layers']['4/0']['polygons']:ax.add_patch(PathPatch(geometry_path(shapely.Polygon(v)),fc='#397c85',ec='none'))
 ax.add_patch(PathPatch(geometry_path(piece['movable']),fc='#9fb3c8',alpha=.22,ec='#5d7894',lw=1.1))
 ax.add_patch(PathPatch(geometry_path(piece['moved']),fc='#e07a2f',alpha=.80,ec='#8f4712',lw=1.1))
 if piece['bridge'] is not None:ax.add_patch(PathPatch(geometry_path(piece['bridge']),fc='#f5b955',alpha=.80,ec='#a86f16',lw=.8))
 if not piece['cut'].is_empty:ax.plot(*piece['cut'].xy,color='#8a4b3a',ls=(0,(6,3)),lw=1.3)
 if piece['seam'] is not None:
  (x0,y0),(x1,y1)=piece['seam']
  ax.plot([x0,x1],[y0,y1],color='#2f7a52',ls=(0,(2,2)),lw=.9)
  ax.plot([x0,x1],[y0,y1],'o',color='#2f7a52',ms=3.4)
 ax.annotate(f"位移 {piece['shift_um']:.1f} μm · 裂隙 {piece['gap_um']:.1f} μm · 补线 4 个端点围成多边形 {piece['bridge_area_um2']:.0f} μm²",xy=(piece['moved'].centroid.x,piece['moved'].bounds[3]),
             xytext=(-190.,120.),fontsize=10,color='#33475a',arrowprops=dict(arrowstyle='->',color='#8e9da8',lw=.9))
 ax.set(xlim=(-260,200),ylim=(-150,180),aspect='equal',xlabel='X / μm',ylabel='Y / μm',
        title='固定RDL重构预演：同一网络的落盘合成一条串、只切一刀，切口垂直于该串走线（与两侧各成 90°），\n移动区随DIE移动，裂隙由两面切口的 4 个端点围成的多边形盖住（是否短路都补）\n'
              f"移动区面积 {piece['movable_area_um2']:.1f} μm² · 切口缝长 {piece['bridge_length_um']:.1f} μm · 补线面积 {piece['bridge_area_um2']:.0f} μm² · 切口距最远落盘 {piece['cut_distance_um']:.1f} μm（2.5 个落盘直径）")
 ax.grid(color='#edf1f4',lw=.6);ax.set_axisbelow(True)
 ax.legend(handles=[Patch(fc='none',ec='#acb9c5',ls='--',label='名义固定RDL'),Patch(fc='#9fb3c8',alpha=.3,label='移动区原位（冷色）'),
                    Patch(fc='#e07a2f',alpha=.8,label='重构后的铜（暖色）'),Patch(fc='#f5b955',alpha=.8,label='补线（切口 4 端点围成）'),
                    Line2D([],[],color='#8a4b3a',ls=(0,(6,3)),label='切口线')],loc='lower center',bbox_to_anchor=(.5,-.30),ncol=2,frameon=False,fontsize=9)
 save(fig,'V21_fixed_rdl_reconstruction')
 recon=dict(source='examples/示例F_逐芯片幅度与迭代窗口.gds',pieces=f['reconstruction']['summary']['pieces'],bridges=f['reconstruction']['summary']['bridges'],
            shift_um=piece['shift_um'],seam_um=piece['gap_um'],seam_length_um=piece['bridge_length_um'],
            cover_area_um2=piece['bridge_area_um2'],cut_distance_um=piece['cut_distance_um'],
            landing_diameters=f['reconstruction']['parameters']['landing_diameters'],
            movable_area_um2=piece['movable_area_um2'],scope=f['reconstruction']['scope'])
 e.close()
 (ROOT/'results/figures_V23.json').write_text(json.dumps(dict(channels=values,reconstruction=recon),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
if __name__=='__main__':main()
