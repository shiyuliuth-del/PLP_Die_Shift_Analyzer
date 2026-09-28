"""Reproducible paper-style vector/300-dpi figures from the shipped engineering case."""
from pathlib import Path
import sys,json
import numpy as np
import matplotlib
matplotlib.use('Agg');import matplotlib.pyplot as plt
from matplotlib.patches import PathPatch,FancyBboxPatch,FancyArrowPatch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_v10 import fixture
from die_shift_studio.deformation import DeformationPreview,smooth_weight
from die_shift_studio.settings import ViewSettings
from die_shift_studio.plot_geometry import geometry_path
plt.rcParams.update({'font.family':['Noto Sans CJK JP','DejaVu Sans'],'font.size':10,'axes.unicode_minus':False,'svg.fonttype':'none','axes.spines.top':False,'axes.spines.right':False})
d,q,c,cache,p,m,res=fixture();z=np.tile([35.,35,.01],(3,1));current=m.current(z,p);dp=DeformationPreview(m,p,res,ViewSettings());out=dp.frame(z,current);value=out['replacements']['5/0'][0];co=next(co for co in dp.components if co.indices==value['indices'])
fig=plt.figure(figsize=(12,4.1),layout='constrained');grid=fig.add_gridspec(1,3,width_ratios=[1.25,1.25,1]);axes=[fig.add_subplot(grid[i]) for i in range(3)]
def patch(ax,g,color,**kw):ax.add_patch(PathPatch(geometry_path(g),facecolor=color,edgecolor='none',**kw))
for i,ax in enumerate(axes[:2]):
 patch(ax,co.poly if i==0 else value['geometry'],'#ccdfef')
 if i:patch(ax,value['dark'],'#8cabca')
 for a in co.anchors:patch(ax,a,'#79649b')
 for r in co.records:
  via=r['poly'] if i==0 else m.moved(r['poly'],r['owner'],z);patch(ax,via,'#287e8f')
 ax.set(xlim=(-150,950),ylim=(-150,220),xlabel='局部 X / μm',ylabel='局部 Y / μm');ax.set_aspect('equal');ax.grid(alpha=.12)
 axes[0].set_title('a  原始固定RDL与固定连接端',loc='left');axes[1].set_title('b  验证通过后整体替换',loc='left')
axes[1].annotate('未型变段',xy=(650,0),xytext=(530,140),arrowprops={'arrowstyle':'-','color':'#8b9eb1'},color='#435d78')
axes[1].annotate('深色：孔盘与连续过渡',xy=(150,30),xytext=(-100,-135),arrowprops={'arrowstyle':'-','color':'#8b9eb1'},color='#435d78')
t=np.linspace(0,1,501);axes[2].plot(t,smooth_weight(t,'C1'),color='#709dbd',lw=2,label='C1 · 三次 Bézier');axes[2].plot(t,smooth_weight(t,'C2'),color='#99b699',lw=2,label='C2 · 五次 Bézier');axes[2].set(xlabel='归一化过渡距离  t',ylabel='位移权重  w',xlim=(0,1),ylim=(-.04,1.04));axes[2].set_title('c  固定区交界的连续性',loc='left');axes[2].legend(frameon=False,fontsize=8);axes[2].grid(alpha=.12)
for ext in ('png','svg'):fig.savefig(ROOT/'figures'/('V10_deformation.'+ext),dpi=300)
plt.close(fig)
fig,ax=plt.subplots(figsize=(11,3.9));ax.set_xlim(0,11);ax.set_ylim(0,3.9);ax.axis('off')
def block(x,y,w,h,text,color):
 ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle='round,pad=0.12,rounding_size=0.06',facecolor=color,edgecolor='#b7c9d8',lw=.8));ax.text(x+w/2,y+h/2,text,ha='center',va='center',fontsize=10,linespacing=1.7)
def arrow(a,b):ax.add_patch(FancyArrowPatch(a,b,arrowstyle='-|>',mutation_scale=13,color='#97acbd',lw=1.))
block(.2,2.1,2.4,1.1,'已评估的真实动静接口\n排除不足与概率待细化','#e8eff7');block(3.15,2.1,2.4,1.1,'合并来源铜图形\n保留固定端，选 C1 / C2','#e6f0ed');block(6.1,2.1,2.4,1.1,'局部型变＋逐帧校验\n线宽 / 线距 / 连接 / 无折叠','#eef0e3')
arrow((2.73,2.65),(3,2.65));arrow((5.68,2.65),(5.95,2.65));block(6.1,.3,2.4,1.05,'通过：替换整铜连通区\n型变区深色，不留旧铜','#ddece7');block(9,.3,1.7,1.05,'未解：保留原图\n列出具体原因','#f4e7e9');arrow((7.3,1.97),(7.3,1.49));arrow((8.55,2.65),(9.9,1.49));ax.text(.25,.95,'视窗外裁剪＋35%冗余区\n静态概率与固定几何缓存\n后台准备验证帧 → 有限时长播放',ha='left',va='center',color='#58728b',linespacing=1.7)
fig.tight_layout()
for ext in ('png','svg'):fig.savefig(ROOT/'figures'/('V10_flow.'+ext),dpi=300,bbox_inches='tight')
plt.close(fig)
print(json.dumps(dict(accepted=out['accepted_count'],span_um=value['span_um'],diagnostics=out['diagnostics']),ensure_ascii=False))
