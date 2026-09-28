"""Reproducible light-palette figures for the contact-screening revision."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle,FancyBboxPatch
from matplotlib.colors import LinearSegmentedColormap
from scipy.stats import norm

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'figures'
plt.rcParams.update({'font.family':['Noto Sans CJK JP','DejaVu Sans'],'font.size':10,'axes.linewidth':.65,'axes.unicode_minus':False,'svg.fonttype':'none'})
INK='#34495b'
def save(fig,name):
    fig.savefig(OUT/(name+'.png'),dpi=300,bbox_inches='tight',facecolor='white')
    vector=OUT/(name+'.svg');fig.savefig(vector,bbox_inches='tight',facecolor='white')
    vector.write_text('\n'.join(line.rstrip() for line in vector.read_text().splitlines())+'\n')
    plt.close(fig)

fig,ax=plt.subplots(figsize=(11.5,2.3));ax.axis('off');ax.set(xlim=(0,12),ylim=(0,2))
labels=[('GDS 与物理层序','异常图形 → 玫红框\n忽略异常 / 非金属连接'),('名义几何网络','相邻金属层正面积交叠\n同层接触 → 网络传递闭包'),('其他网络接触','有限 σ：保守包络\n∞：非零概率可达性'),('方案与位置证据','综合 Γ + 结构 / 容量\n红圈定位 + 三方案理由')]
for i,(title,body) in enumerate(labels):
    x=3*i
    ax.add_patch(FancyBboxPatch((x+.1,.3),2.6,1.4,boxstyle='round,pad=0.08,rounding_size=.09',facecolor=['#edf3f8','#eaf3ee','#f2edf6','#f7f0e9'][i],edgecolor='#bfcbd6',lw=.8))
    ax.text(x+1.4,1.3,title,ha='center',va='center',weight='medium',color=INK)
    ax.text(x+1.4,.8,body,ha='center',va='center',fontsize=9,color=INK,linespacing=1.7)
    if i<3:ax.annotate('',xy=(x+3.03,1),xytext=(x+2.82,1),arrowprops=dict(arrowstyle='->',color='#93a7b7',lw=1.2))
save(fig,'v050_workflow')

fig,axs=plt.subplots(1,3,figsize=(12,3.8),layout='constrained')
from shapely.geometry import box
via=box(25,-5,35,5)
ax=axs[0]
ax.add_patch(Rectangle((-20,-10),40,20,facecolor='#d7e9e1',edgecolor='none'))
ax.add_patch(Rectangle((50,-10),20,20,facecolor='#dfd2ea',edgecolor='none'))
ax.add_patch(Rectangle((25,-5),10,10,facecolor='#387b80',edgecolor='none',zorder=4))
for radius,color in [(10,'#479c96'),(15,'#ad7aac')]:
    a=np.asarray(via.buffer(radius,quad_segs=32).exterior.coords);ax.plot(*a.T,color=color,lw=1.1,ls=(0,(4,3)))
ax.plot([0,30],[0,0],color='#d78b45',ls='--',lw=1,zorder=5)
ax.plot([0,50],[0,-5],color='#929ba3',ls='--',lw=.9,zorder=5)
ax.scatter([50],[-5],facecolors='none',edgecolors='#c74c61',s=60,zorder=6)
ax.text(-17,14,'本网络',color='#527766',fontsize=9);ax.text(49,14,'其他网络',color='#856898',fontsize=9)
ax.text(-17,-27,'包围量 10 μm + 线距 5 μm',color=INK,fontsize=9)
ax.set(title='a  当前状态：碰到其他网络',xlabel='X / μm',ylabel='Y / μm',xlim=(-25,76),ylim=(-32,28));ax.set_aspect('equal')

ax=axs[1];grid=np.linspace(-20,20,81);s=25/6
mass=np.diff(norm.cdf(grid/s));density=np.outer(mass,mass)
cmap=LinearSegmentedColormap.from_list('probability',['#f6f9fc','#d8e6ef','#a9c8dc','#597f9f'])
im=ax.pcolormesh(grid,grid,density*100,cmap=cmap,shading='flat',rasterized=True)
ax.set(title='b  单孔中心概率：相同网格',xlabel='ΔX / μm',ylabel='ΔY / μm');ax.set_aspect('equal')
cb=fig.colorbar(im,ax=ax,shrink=.68,pad=.025);cb.set_label('每格概率 / %',fontsize=9);cb.outline.set_linewidth(.5)
ax.text(0,-30,'25 μm = 6σ；格宽 0.5 μm',ha='center',color=INK,fontsize=9)

ax=axs[2];ax.add_patch(Rectangle((0,0),20,16,facecolor='#e2eaf0',edgecolor='none'))
ax.plot([30,50,30,50,30],[0,16,16,0,0],color='#c3cad2',lw=.9)
ax.add_patch(Rectangle((28,-2),24,20,fill=False,edgecolor='#d72b91',lw=.75,ls=(0,(3,3))))
ax.text(10,23,'有效图形',ha='center',color=INK,fontsize=9);ax.text(40,23,'自交图形',ha='center',color=INK,fontsize=9)
ax.text(27,-13,'异常图形跳过计算\n定位框与所在层同步显隐',ha='center',color=INK,fontsize=9,linespacing=1.7)
ax.set(title='c  异常图形：保留位置提示',xlabel='X / μm',ylabel='Y / μm',xlim=(-8,62),ylim=(-20,32));ax.set_aspect('equal')
for ax in axs:
    ax.spines[['top','right']].set_visible(False);ax.grid(alpha=.10,lw=.5);ax.tick_params(labelsize=9)
save(fig,'v050_contact_probability')
print('V050_FIGURES_PASS')
