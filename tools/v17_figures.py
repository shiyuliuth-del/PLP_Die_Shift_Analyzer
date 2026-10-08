"""Scientific V17 figures: inspected model geometry and identical probability data."""
from pathlib import Path
import sys,json,math
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import PowerNorm,LinearSegmentedColormap,ListedColormap
from matplotlib.patches import PathPatch
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.probability_overlay import probability_maps
from die_shift_studio.plot_geometry import geometry_path
font=next((n for n in ('Noto Sans CJK SC','Noto Sans CJK JP','Microsoft YaHei') if n in {f.name for f in font_manager.fontManager.ttflist}),'DejaVu Sans')
plt.rcParams.update({'font.family':font,'font.size':10,'svg.fonttype':'none','axes.spines.top':False,'axes.spines.right':False})
INK='#344e62'
def save(fig,name):
 for ext in ('png','svg'):fig.savefig(ROOT/'figures'/(name+'.'+ext),dpi=300,bbox_inches='tight')
 plt.close(fig)
fig,axes=plt.subplots(1,2,figsize=(10,3.8),layout='constrained')
for ax,metric,title,unit in ((axes[0],'accepted','a  通过最终验收的铜连通区','数量'),(axes[1],'cold_frame_ms','b  首次指定状态求解','时间 / s')):
 for i,w in enumerate((15,20)):
  old=json.loads((ROOT/'results'/('baseline_GD55_15_V17.json' if w==15 else 'baseline_GD55_V17.json')).read_text());new=json.loads((ROOT/'results'/f'performance_GD55_{w}_V17.json').read_text())
  for shift,value,color,label in ((-.17,old[metric],'#c4d5e2','V16'),(.17,new[metric],'#8fbcaa','V17')):
   if metric.endswith('_ms'):value/=1000
   ax.bar(i+shift,value,width=.29,color=color,label=label if i==0 else None)
   ax.text(i+shift,value+1,f'{value:.1f}' if metric.endswith('_ms') else str(value),ha='center',color=INK,fontsize=9)
 ax.set(xticks=[0,1],xticklabels=['线宽/线距 15/15 μm','线宽/线距 20/20 μm'],ylabel=unit,title=title);ax.set_ylim(0,ax.get_ylim()[1]*1.15);ax.legend(frameon=False);ax.grid(axis='y',alpha=.12);ax.set_axisbelow(True)
save(fig,'V17_comparison')
q=json.loads((ROOT/'examples/示例J_短宽孔盘与DRC容差.json').read_text());data=read_gds(ROOT/'examples'/q['gds_path']);case=build_case(data,q['roles'],layer_order=q['layer_order']);p=Parameters.from_dict(q['parameters']);e=PreviewEngine(GeometryCache(data),case,q['roles'],{},p,{},case['connections']);e.configure_deformation(True,ViewSettings());f=e.frame(np.array([[-25.,-25.,0]]));replacement=f['deformation']['replacements']['5/0'][0];assert replacement['verified'] and replacement['mode']=='directional_pad'
fig,axes=plt.subplots(1,2,figsize=(8,4.5),layout='constrained')
for ax,after in zip(axes,(False,True)):
 g=replacement['geometry'] if after else e.deformer.components[0].poly
 ax.add_patch(PathPatch(geometry_path(g),fc='#d7e5ef',ec='#8caac0',lw=.7))
 if after:ax.add_patch(PathPatch(geometry_path(replacement['dark']),fc='#8badc4',ec='none'))
 via=e.contact.moved(e.contact.records[0]['poly'],0,f['states']) if after else e.contact.records[0]['poly']
 ax.add_patch(PathPatch(geometry_path(via),fc='#28748a',ec='none'))
 fixed=e.deformer.components[0].anchors[0];ax.add_patch(PathPatch(geometry_path(fixed),fc='#8d76a6',ec='none'))
 ax.add_patch(PathPatch(geometry_path(via.buffer(15)),fc='none',ec='#589b94',ls='--',lw=.8))
 ax.set(xlim=(-100,80),ylim=(-95,145),aspect='equal',xlabel='X / μm',ylabel='Y / μm',title='b  方向性过渡：固定端保持原位' if after else 'a  短宽孔盘与近端固定孔')
save(fig,'V17_directional_pad')
prob=probability_maps(e.contact.cache,e.contact.bound,q['roles'],p,masks=e.contact.interface_masks,settings=ViewSettings(probability_method='quadrature'))['maps'][0]
fig,axes=plt.subplots(1,3,figsize=(11.5,3.5),layout='constrained')
for ax,gamma,colors,title in zip(axes,(1,.35,.35),(['#ecf5f8','#abd1df','#287e98'],['#afd2e5','#559cbb','#19557a'],['#f3d49d','#dea34b','#934513']),('a  V16浅色显示','b  V17蓝青 / 低概率增强','c  V17暖橙 / 同一概率网格')):
 base=LinearSegmentedColormap.from_list('prob',colors);x=np.linspace(0,1,4096);rgba=base(x);rgba[:,3]=np.sqrt(x);cmap=ListedColormap(rgba);cmap.set_bad((0,0,0,0))
 image=ax.imshow(np.ma.masked_where(prob['probability']==0,prob['probability']),extent=prob['extent'],origin='lower',cmap=cmap,norm=PowerNorm(gamma,0,1),alpha=.42)
 ax.set(xlim=(-45,45),ylim=(-45,45),aspect='equal',xlabel='X / μm',ylabel='Y / μm',title=title);ax.grid(alpha=.08)
 bar=fig.colorbar(image,ax=ax,orientation='horizontal',fraction=.04,pad=.15);bar.set_ticks([0,.5,1] if gamma==1 else [0,.01,.1,1]);bar.set_ticklabels(['0','50%','100%'] if gamma==1 else ['0','1%','10%','100%']);bar.ax.tick_params(labelsize=8)
save(fig,'V17_probability_palettes');e.close();print('V17_FIGURES_PASS')
