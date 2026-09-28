"""Vector and 300 dpi scientific figures from actual V11 numerical output."""
from pathlib import Path
import sys,json
import numpy as np
import matplotlib
matplotlib.use('Agg');import matplotlib.pyplot as plt
from matplotlib.patches import PathPatch
from matplotlib.colors import LinearSegmentedColormap
from dataclasses import replace
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_v11 import fixture
from test_v050 import fixture as small
from die_shift_studio.settings import ViewSettings
from die_shift_studio.probability_overlay import probability_maps
from die_shift_studio.deformation import DeformationPreview
from die_shift_studio.plot_geometry import geometry_path
plt.rcParams.update({'font.family':['Noto Sans CJK JP','DejaVu Sans'],'font.size':10,'axes.unicode_minus':False,'svg.fonttype':'none','axes.spines.top':False,'axes.spines.right':False})
def save(fig,name):
 for ext in ('png','svg'):fig.savefig(ROOT/'figures'/(name+'.'+ext),dpi=300,bbox_inches='tight')
 plt.close(fig)
def patch(ax,g,color,**kw):ax.add_patch(PathPatch(geometry_path(g),facecolor=color,edgecolor='none',**kw))
d,roles,case,cache,con,m,p=small();p=replace(p,single_bound_x_um=35,single_bound_y_um=35,bound_angle_deg=1)
fig,axes=plt.subplots(1,3,figsize=(11.8,3.7),layout='constrained');audit=[]
for ax,k in zip(axes,(1,6,12)):
 out=probability_maps(cache,m.bound,roles,replace(p,target_nsigma=k),masks=m.interface_masks,settings=ViewSettings(probability_method='quadrature',quadrature_order=25,acceleration='cpu'))
 q=out['maps'][0];image=ax.imshow(np.ma.masked_equal(q['probability'],0),extent=q['extent'],origin='lower',cmap=LinearSegmentedColormap.from_list('occupancy',['#f1f6f7','#b7d6df','#397f98']),vmin=0,vmax=1,interpolation='nearest')
 ax.plot(*q['input_envelope'].T,color='#849aa9',ls='--',lw=.8);ax.set(xlim=(-47,47),ylim=(-47,47),xlabel='局部 X / μm',ylabel='局部 Y / μm',title=f'{k}σ · 相同最大幅度 ±35 μm');ax.set_aspect('equal');ax.grid(alpha=.1)
 audit.append(dict(k=k,cell_um=out['cell_um'],integrated_area=q['integrated_area_um2'],edge=q['edge_probability'],mass_error=q['convolution_mass_error']))
fig.colorbar(image,ax=axes,shrink=.83,pad=.015,label='过孔实体覆盖概率');save(fig,'V11_probability_support')
d,q,c,cache,p,m=fixture();z=np.array([[80.,0,0],[80.,0,0]]);initial=m.current(z,p);dp=DeformationPreview(m,p,{},ViewSettings());final=dp.frame(z,initial)
# Reproduce the first accepted component with the same public search and checks.
cB=next(co for co in dp.components if co.records[0]['owner']==1);vias=[m.moved(r['poly'],r['owner'],z) for r in m.records]
g,field,span=dp.search(cB,cB.records,cB.anchors,z,vias,dp._snapshot({}));first=dp._replacements({(cB.key,cB.indices):dict(component=cB,geometry=g,field=field,span=span)})
fig,axes=plt.subplots(1,3,figsize=(12,3.7),layout='constrained')
for ax,replacements,title in zip(axes,({},first,final['replacements']),('初始：1 个不足窗口','迭代 1：释放原不足窗口','迭代 2：两个孔盘完成适配')):
 replace_indices={i for v in replacements.get('5/0',[]) for i in v['indices']}
 for i,g in enumerate(cache.layers['5/0']['geometry']):
  if i not in replace_indices:patch(ax,g,'#d2e1ed')
 for v in replacements.get('5/0',[]):patch(ax,v['geometry'],'#d2e1ed');patch(ax,v['dark'],'#94b3cc')
 contacts=m.current(z,p,replacements)
 for r in m.records:
  via=m.moved(r['poly'],r['owner'],z);patch(ax,via,'#377f8e');ax.plot([r['center'][0],via.centroid.x],[0,0],color='#d3995c',ls='--',lw=1)
 for intr in contacts['intrusions']:patch(ax,intr['geometry'],'#de7186',alpha=.65)
 ax.set(xlim=(-70,250),ylim=(-85,85),xlabel='局部 X / μm',ylabel='局部 Y / μm',title=title);ax.set_aspect('equal');ax.grid(alpha=.12)
save(fig,'V11_iteration')
# Full geometry provides chip-size context independent of the contact close-up.
fig,ax=plt.subplots(figsize=(7.8,4.6),layout='constrained')
colors={'128/0':'#e6edf5','3/0':'#c6dece','5/0':'#cddded','4/0':'#377f8e','6/0':'#8d78ad'}
for layer in ('128/0','3/0','5/0','4/0','6/0'):
 for g in cache.layers[layer]['geometry']:patch(ax,g,colors[layer])
for i,die in enumerate(c['dies']):ax.text(die.centroid.x,die.centroid.y,f'DIE {i+1}\n600 × 600 μm\nB = {p.die_bounds[i]}',ha='center',va='center',color='#42576d',fontsize=9)
ax.set(xlim=(-2000,2000),ylim=(-1300,1300),xlabel='X / μm',ylabel='Y / μm',title='示例 F · 两颗芯片，独立幅度，芯片外扇出');ax.set_aspect('equal');ax.grid(alpha=.12);save(fig,'V11_case_F')
(ROOT/'results/figure_checks_V11.json').write_text(json.dumps(dict(probability=audit,iterations=final['iterations'],released=final['released_count']),ensure_ascii=False,indent=2)+'\n')
