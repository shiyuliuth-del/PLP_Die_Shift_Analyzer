"""Publication-style vector and 300 dpi figures from actual V12 copper geometry."""
from pathlib import Path
import sys,json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import PathPatch,Patch
from dataclasses import replace
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_v12 import fixture
from die_shift_studio.deformation import DeformationPreview
from die_shift_studio.settings import ViewSettings
from die_shift_studio.plot_geometry import geometry_path
plt.rcParams.update({'font.family':['Noto Sans CJK JP','DejaVu Sans'],'font.size':10,'axes.unicode_minus':False,'svg.fonttype':'none','axes.spines.top':False,'axes.spines.right':False,'axes.linewidth':.6})
COL=dict(source='#dae5ee',dark='#8eafc9',via='#317987',fixed='#88739e',die='#edf2ed',pink='#d981ac')
def draw(ax,g,color,**kwargs):
    defaults=dict(facecolor=color,edgecolor='none');defaults.update(kwargs)
    ax.add_patch(PathPatch(geometry_path(g),**defaults))
def style(ax,title,xlim,ylim):
    ax.set(title=title,xlim=xlim,ylim=ylim,xlabel='X / μm',ylabel='Y / μm');ax.set_aspect('equal');ax.grid(alpha=.12)
def save(fig,name):
    for ext in ('png','svg'):fig.savefig(ROOT/'figures'/(name+'.'+ext),dpi=300,bbox_inches='tight')
    plt.close(fig)
d,q,case,cache,p,m=fixture();z=np.array([[1.,1,0],[0,35,0]]);dp=DeformationPreview(m,p,{},ViewSettings());final=dp.frame(z,m.current(z,p))
fig,axes=plt.subplots(1,2,figsize=(11,6),layout='constrained')
for ax,is_after in zip(axes,(False,True)):
    for i,g in enumerate(case['dies']):draw(ax,m.moved(g,i,z) if is_after else g,COL['die'],edgecolor='#aac3b0',lw=.7)
    for i,die in enumerate(case['dies']):
        g=m.moved(die,i,z) if is_after else die;ax.text(g.centroid.x,g.centroid.y,f'DIE {i+1}\n600 × 600 μm',ha='center',va='center',fontsize=9,color='#466353')
    for i,g in enumerate(cache.layers['3/0']['geometry']):draw(ax,m.moved(g,int(m.bound.owners['3/0'][i]),z) if is_after else g,'#c8ddcf')
    replacement=final['replacements']['5/0'] if is_after else []
    hidden={i for v in replacement for i in v['indices']}
    for i,g in enumerate(cache.layers['5/0']['geometry']):
        if i not in hidden:draw(ax,g,COL['source'])
    for v in replacement:
        draw(ax,v['geometry'],COL['source']);draw(ax,v['dark'],COL['dark'])
        if not v['adaptation'].is_empty:draw(ax,v['adaptation'],'none',edgecolor='#4e7594',ls=(0,(4,3)),lw=1.)
    for r in m.records:draw(ax,m.moved(r['poly'],r['owner'],z) if is_after else r['poly'],COL['via'])
    for g in cache.layers['6/0']['geometry']:draw(ax,g,COL['fixed'])
    style(ax,'b  适配后：孔盘调整 + 三段追溯' if is_after else 'a  原始连通图形',(-1050,1150),(-1250,1150))
axes[1].annotate('780 μm 适配段\n两端远离拐角',xy=(450,969),xytext=(430,550),ha='center',arrowprops=dict(arrowstyle='-',color='#627d95',lw=.8),fontsize=9)
fig.legend(handles=[Patch(facecolor=COL['source'],label='固定部分'),Patch(facecolor=COL['dark'],label='运动与适配部分'),Patch(facecolor=COL['via'],label='运动过孔'),Patch(facecolor=COL['fixed'],label='固定过孔')],loc='lower center',bbox_to_anchor=(.5,-.045),ncol=4,frameon=False)
save(fig,'V12_trace_adaptation')
fig,axes=plt.subplots(1,3,figsize=(10.5,3.5),layout='constrained');source=dp.components[0].poly;v=next(v for v in final['replacements']['5/0'] if v['mode']=='local');r=next(r for r in m.records if r['owner']==0)
for ax,index in zip(axes,range(3)):
    via=m.moved(r['poly'],r['owner'],z)
    draw(ax,source if index<2 else v['geometry'],COL['source'])
    if index==2:draw(ax,v['dark'],COL['dark'])
    draw(ax,r['poly'] if index==0 else via,COL['via'])
    if index:
        draw(ax,via.buffer(p.annulus_um),'none',edgecolor='#a38b5d',ls='--',lw=1)
    style(ax,('a  原始孔盘','b  平移 (1, 1) μm','c  包围量作为输出约束')[index],(-35,50),(-886,-814))
axes[2].text(6,-880,'a = 12 μm',ha='center',color='#836d48',fontsize=9)
fig.suptitle('局部放大：过孔仍在原孔盘内，也执行适配',fontsize=11)
save(fig,'V12_small_shift')
fig,ax=plt.subplots(figsize=(10.5,2.3),layout='constrained');ax.set_axis_off()
labels=['沿实际网络追溯\n运动孔 → 固定孔','优先局部孔盘调整\n输出满足包围量','选择长直适配段\n固定端保持不动','完整几何校验\n线宽 / 线距 / 内角']
for i,label in enumerate(labels):
    x=.015+i*.25
    ax.text(x+.108,.6,label,ha='center',va='center',transform=ax.transAxes,bbox=dict(boxstyle='round,pad=.8',facecolor=['#edf3ef','#eef3f8','#e8f0f6','#f5edf2'][i],edgecolor='#ccd6dd',lw=.7))
    if i<3:ax.annotate('',xy=(x+.243,.6),xytext=(x+.222,.6),xycoords=ax.transAxes,arrowprops=dict(arrowstyle='->',color='#7e93a3',lw=1))
ax.text(.5,.08,'通过 → 释放窗口并继续迭代     |     有 DRC → 显示最佳候选与粉色违规区域     |     拓扑不合法 → 保留源图形',ha='center',va='center',transform=ax.transAxes,fontsize=9,color='#556b7d')
save(fig,'V12_workflow')
(ROOT/'results/figure_checks_V12.json').write_text(json.dumps(dict(accepted=final['accepted_count'],best_effort=final['best_effort_count'],violations=len(final['violations']),modes=[v['mode'] for v in final['replacements']['5/0']],spans_um=[v['span_um'] for v in final['replacements']['5/0']]),indent=2)+'\n')
