"""Rerunnable light scientific figures from V9's actual numerical kernels."""
from pathlib import Path
import sys,json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle,FancyArrowPatch
from matplotlib.colors import LinearSegmentedColormap
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_v050 import fixture
from die_shift_studio.core import Parameters,uncertainty_bound
from die_shift_studio.probability_overlay import probability_maps
from die_shift_studio.settings import ViewSettings
from die_shift_studio.contact_probability import contact_probability
from die_shift_studio.distribution import normal_interval
from dataclasses import replace
plt.rcParams.update({'font.family':['Noto Sans CJK JP','DejaVu Sans'],'font.size':9,'axes.spines.top':False,'axes.spines.right':False,'svg.fonttype':'none','axes.unicode_minus':False,'text.color':'#344b61','axes.labelcolor':'#344b61','axes.linewidth':.7})
def save(fig,name):
 for ext in ('png','svg'):fig.savefig(ROOT/f'figures/V9_{name}.{ext}',dpi=300,bbox_inches='tight',pad_inches=.12)
 plt.close(fig)
fig,ax=plt.subplots(1,3,figsize=(12,3.65),layout='constrained');x=np.linspace(-45,45,1000)
for k,color in ((1,'#9dbdb3'),(3,'#b4a3c5'),(6,'#8cabca')):
 sigma=35/k;y=np.exp(-.5*(x/sigma)**2)/(np.sqrt(2*np.pi)*sigma)/normal_interval(-k,k);y[np.abs(x)>35]=0
 ax[0].plot(x,y,color=color,lw=1.5,label=f'{k}σ');ax[0].fill_between(x,0,y,color=color,alpha=.13)
ax[0].axvline(-35,color='#738497',ls=':',lw=.8);ax[0].axvline(35,color='#738497',ls=':',lw=.8)
ax[0].set(xlabel='芯片平移 DX / μm',ylabel='条件概率密度 / μm⁻¹',title='a  最大幅度始终为 ±35 μm',xlim=(-45,45));ax[0].legend(frameon=False);ax[0].grid(alpha=.12)
*_,model,p=fixture();p=replace(p,target_nsigma=1,single_bound_x_um=30,single_bound_y_um=30)
q=probability_maps(model.cache,model.bound,model.roles,p,masks=model.interface_masks,settings=ViewSettings(probability_method='quadrature',probability_cell_um=.5))['maps'][0]
occupancy_integral=q['integrated_area_um2']
im=ax[1].imshow(q['probability'],origin='lower',extent=q['extent'],cmap=LinearSegmentedColormap.from_list('scientific',['#f2f6f8','#b6cedc','#678faf']),vmin=0,vmax=q['probability'].max())
a=q['input_envelope'];ax[1].plot(a[:,0],a[:,1],ls=':',color='#677f99',lw=1);ax[1].add_patch(Rectangle((-5,-5),10,10,fill=False,edgecolor='#426d81',lw=1))
ax[1].set(xlabel='X / μm',ylabel='Y / μm',title='b  整孔覆盖概率与输入包络');fig.colorbar(im,ax=ax[1],orientation='horizontal',fraction=.055,pad=.10,label='实体覆盖概率')
kvals=[1,3,6,12];lo=[];hi=[];truth=[];threshold=[]
for k in kvals:
 pp=replace(p,target_nsigma=k,single_bound_x_um=35,single_bound_y_um=0);v=contact_probability(model,model.records[0],pp)
 lo.append(max(v['lower'],1e-36));hi.append(v['upper']);truth.append(float(normal_interval(30/(35/k),k)/normal_interval(-k,k)));threshold.append(v['threshold'])
xx=np.arange(4);ax[2].vlines(xx,lo,hi,color='#a8c1d3',lw=5,label='计算上下界');ax[2].scatter(xx,truth,s=23,color='#5d879b',label='解析值',zorder=3);ax[2].plot(xx,threshold,'--s',ms=4,color='#bb99a8',label='允许上限')
ax[2].annotate('下界为0',(0,1e-33),xytext=(.35,1e-31),fontsize=8,color='#748c9c',arrowprops=dict(arrowstyle='-',color='#a8c1d3',lw=.7))
ax[2].set(yscale='log',xticks=xx,xticklabels=[f'{k}σ' for k in kvals],ylim=(1e-34,1),ylabel='每接口接触概率',title='c  概率区间与解析值一致');ax[2].legend(frameon=False,fontsize=7);ax[2].grid(axis='y',alpha=.12)
save(fig,'model')
fig,ax=plt.subplots(figsize=(10.5,2.6),layout='constrained');ax.set(xlim=(0,10.5),ylim=(0,2.6));ax.axis('off')
boxes=[(.05,2.25,'GDS + 用途 + 层序\n真实接口、几何网络、所属DIE','#e4edf5'),(2.85,2.0,'输入最大偏移\n目标k → 截断正态分布','#e4f0e9'),(5.4,2.05,'空白行 + 有限k\npL > α 才判不足','#eee6f2'),(8,2.3,'极端 / 手动 / ∞\n当前接触 → 二值不足','#f3e9df')]
for x,w,t,c in boxes:
 ax.add_patch(Rectangle((x,.8),w,1.1,facecolor=c,edgecolor='#a4b1bd',lw=.8));ax.text(x+w/2,1.35,t,ha='center',va='center',fontsize=10)
for a,b in ((2.3,2.85),(4.85,5.4),(7.45,8)):ax.add_patch(FancyArrowPatch((a,1.35),(b,1.35),arrowstyle='->',mutation_scale=12,color='#8195a5'))
ax.text(5.25,.22,'概率云：实体覆盖概率     ·     Space Map：所需线距     ·     两者共用连接归属与输入边界',ha='center',fontsize=10)
save(fig,'flow')
fig,axs=plt.subplots(1,2,figsize=(10,3.5),layout='constrained')
length=np.linspace(400,12000,250)
for aspect,color in ((1,'#87abc4'),(2,'#9dbaa9'),(3,'#b6a3c8')):
 radius=np.hypot(length/2,length/(2*aspect));points=np.column_stack((radius,np.zeros_like(radius)))
 q=uncertainty_bound(points,[0,0],Parameters(multi_bound_x_um=35,multi_bound_y_um=35),3)
 valid=length/aspect>=400
 axs[0].plot(length[valid],q[valid],color=color,label=f'长宽比 {aspect}:1')
axs[0].axvspan(500,10000,color='#eef3f5',zorder=-2);axs[0].set(xlim=(400,12000),xlabel='芯片长边 / μm',ylabel='角点保守偏移上界 / μm',title='a  最大35 μm / 1°，目标6σ');axs[0].legend(frameon=False);axs[0].grid(alpha=.13)
for speed,color in ((1,'#b6a3c8'),(3,'#87abc4'),(5,'#9dbaa9')):
 from die_shift_studio.animation import motion_timing
 distance=np.linspace(0,300,250);times=[motion_timing(ViewSettings(animation_speed=speed),d,10000)[0] for d in distance]
 axs[1].plot(distance,times,color=color,label=f'速度级别 {speed}')
axs[1].set(xlabel='平移 + 转角等效最长路程 / μm',ylabel='计划时长 / ms',title='b  随距离变化，默认上限1000 ms',ylim=(0,1100));axs[1].legend(frameon=False);axs[1].grid(alpha=.13)
save(fig,'size_motion')
(ROOT/'results/analytic_V9.json').write_text(json.dumps(dict(k=kvals,lower=lo,upper=hi,analytic=truth,allowed=threshold,via_area_um2=100,occupancy_integral_um2=occupancy_integral),ensure_ascii=False,indent=2)+'\n')
