"""Scientific evidence for the separation of input limits and probability coverage."""
from pathlib import Path
import sys,json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio.preview import rigid_transform
from die_shift_studio.core import Parameters,uncertainty_bound
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,'axes.spines.top':False,'axes.spines.right':False,'svg.fonttype':'none','text.color':'#344b61','axes.labelcolor':'#344b61'})
fig,ax=plt.subplots(1,3,figsize=(12,3.65),layout='constrained')
ax[0].add_patch(Rectangle((-35,-35),70,70,facecolor='#dcebe5',edgecolor='#779a8c',lw=1))
for x in (-35,35):
 for y in (-35,35):
  ax[0].scatter([x],[y],s=100,facecolor='#c0ccdf',edgecolor='#5f7c9c',zorder=3)
  ax[0].annotate('O = ±1°',(x,y),xytext=(0,10 if y>0 else -18),textcoords='offset points',ha='center',fontsize=8)
ax[0].set(xlim=(-55,55),ylim=(-57,57),xticks=[-35,0,35],yticks=[-35,0,35],xlabel='DIE translation DX / μm',ylabel='DIE translation DY / μm',title='a  Eight input-boundary combinations')
ax[0].set_aspect('equal');ax[0].grid(alpha=.12);ax[0].axhline(0,color='#a7b3c0',lw=.6);ax[0].axvline(0,color='#a7b3c0',lw=.6)
radius=np.linspace(0,12000,301);angle=np.deg2rad(1);points=np.column_stack((radius,np.zeros_like(radius)))
a=rigid_transform(points,np.zeros(2),[35,35,angle]);actual=np.linalg.norm(a-points,axis=1)
ax[1].plot(radius,actual,color='#739f95',lw=1.8,label='Translation + rotation')
ax[1].axhline(np.hypot(35,35),color='#a89cbf',ls='--',lw=1.3,label='Translation alone')
ax[1].fill_between(radius,np.hypot(35,35),actual,color='#dcebe5',alpha=.8)
ax[1].set(xlabel='Distance from connected DIE center / μm',ylabel='Via center displacement / μm',title='b  Via motion includes the rotation lever arm',xlim=(0,12000));ax[1].legend(frameon=False,fontsize=8,loc='upper left');ax[1].grid(alpha=.1)
old=json.loads((ROOT/'results/baseline_v7_for_V8.json').read_text());new=json.loads((ROOT/'results/performance_V8.json').read_text())
xx=np.arange(2);baseline=[old['Qt_preview_median_ms'],old['geometry_median_ms']];updated=[new['Qt_preview_median_ms'],new['geometry_median_ms']]
for shift,values,color,name in [(-.18,baseline,'#c3cce0','V7'),(.18,updated,'#b7d4c5','V8')]:
 bars=ax[2].bar(xx+shift,values,width=.34,color=color,label=name)
 for b,v in zip(bars,values):ax[2].text(b.get_x()+b.get_width()/2,v+3,f'{v:.1f}',ha='center',fontsize=8)
ax[2].set(xticks=xx,xticklabels=['Qt preview','Geometry'],ylabel='Median elapsed time / ms',title='c  GD55: identical manual states')
ax[2].set_ylim(0,max(baseline)*1.24);ax[2].legend(frameon=False,fontsize=8);ax[2].grid(axis='y',alpha=.12);ax[2].set_axisbelow(True)
for ext in ('png','svg'):fig.savefig(ROOT/f'figures/V8_boundary_performance.{ext}',dpi=300)
plt.close(fig)
