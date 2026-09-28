"""V15 300 dpi + editable SVG: verified data, soft scientific palette."""
from pathlib import Path
import sys,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch,FancyArrowPatch
font=next((x for x in ('Noto Sans CJK SC','Noto Sans CJK JP','Microsoft YaHei') if x in {f.name for f in font_manager.fontManager.ttflist}),'DejaVu Sans')
plt.rcParams.update({'font.family':font,'font.size':10,'svg.fonttype':'none','axes.spines.top':False,'axes.spines.right':False})
COLORS=['#dceaf2','#e1eee6','#eee3ef','#f4eadb'];INK='#344e62'
def save(fig,name):
    for ext in ('png','svg'):fig.savefig(ROOT/'figures'/(name+'.'+ext),dpi=300,bbox_inches='tight')
    plt.close(fig)
fig,ax=plt.subplots(figsize=(12,4.8));ax.set(xlim=(0,12),ylim=(0,5));ax.axis('off')
def block(x,y,w,h,title,body,color):
    ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle='round,pad=.08,rounding_size=.12',fc=color,ec='#c2d0da',lw=.8))
    ax.text(x+w/2,y+h*.72,title,ha='center',va='center',fontsize=12,color=INK,weight='bold')
    ax.text(x+w/2,y+h*.33,body,ha='center',va='center',fontsize=10,color=INK,linespacing=1.65)
block(2.7,3.65,6.6,.94,'真实GDS + 工艺 + 每DIE输入边界','几何修复 → 按层连通 → 旋转中心与接口归属','#f0f5f8')
for i,(title,body,out) in enumerate([
 ('空间初筛 U','带宽 / 可用边长\n位移 / 捕获净空间','U≤1候选；最小U优先'),
 ('原图接触概率','截断正态 → 概率上下界\n与目标允许概率比较','超限 / 通过 / 待细化'),
 ('连续安全内盒','独立扩张X/Y/O端点\n整盒几何外包络验证','任意组合无接触的内范围'),
 ('指定状态型变','孔盘 → 三段截面 → 迭代\n全层DRC与固定端复核','合格显示；不合格恢复原图')]):
    x=.2+i*3
    ax.add_patch(FancyArrowPatch((6,3.56),(x+1.25,3.03),arrowstyle='-|>',mutation_scale=11,lw=.9,color='#9baebb'))
    block(x,1.45,2.5,1.48,title,body,COLORS[i]);ax.text(x+1.25,1.02,out,ha='center',va='center',fontsize=9.5,color=INK)
ax.text(6,.38,'初筛不等于制造放行  ·  概率结果不等于安全范围  ·  连续内盒不声明全局最大',ha='center',color='#647b8b',fontsize=10)
save(fig,'V15_flow')
r=json.loads((ROOT/'results/assessment_GD55_V15.json').read_text());rows=r['safe_ranges']['rows']
from die_shift_studio.safe_range_plot import draw_safe_ranges
fig=plt.figure(figsize=(11.5,4.8));draw_safe_ranges(fig,r['safe_ranges'])
save(fig,'V15_safe_ranges')
base=json.loads((ROOT/'results/baseline_GD55_V15.json').read_text());new=json.loads((ROOT/'results/performance_auto_V15.json').read_text())
fig,axes=plt.subplots(1,2,figsize=(9,3.3),layout='constrained')
for ax,values,title,unit in [(axes[0],[base['cold_frame_ms']/1000,new['cold_frame_ms']/1000],'a  首次完整预演（越低越快）','时间 / s'),(axes[1],[base['accepted'],new['accepted']],'b  通过最终校验的固定RDL图形','图形数')]:
    ax.bar(['V14','V15'],values,color=['#d2dee8','#a6c6ba'],width=.48);ax.set(ylabel=unit,title=title,ylim=(0,max(values)*1.23));ax.grid(axis='y',alpha=.13);ax.set_axisbelow(True)
    for i,v in enumerate(values):ax.text(i,v+max(values)*.025,f'{v:.1f}' if unit.endswith('s') else str(v),ha='center',color=INK)
save(fig,'V15_performance')
print('V15_FIGURES_PASS')
