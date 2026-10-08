"""V16 report figures from saved evidence; 300 dpi + editable SVG."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch,FancyArrowPatch
from die_shift_studio.safe_range_plot import draw_safe_ranges
font=next((n for n in ('Noto Sans CJK SC','Noto Sans CJK JP','Microsoft YaHei') if n in {f.name for f in font_manager.fontManager.ttflist}),'DejaVu Sans')
plt.rcParams.update({'font.family':font,'font.size':10,'svg.fonttype':'none','axes.spines.top':False,'axes.spines.right':False})
INK='#344e62'
def save(fig,name):
    for ext in ('png','svg'):fig.savefig(ROOT/'figures'/(name+'.'+ext),dpi=300,bbox_inches='tight')
    plt.close(fig)
r=json.loads((ROOT/'results/assessment_GD55_V16.json').read_text());fig=plt.figure(figsize=(12,6.7));draw_safe_ranges(fig,r['safe_ranges'],r['model_yield']);save(fig,'V16_safe_ranges')
fig,ax=plt.subplots(figsize=(13,4.1));ax.set(xlim=(0,15),ylim=(0,4.8));ax.axis('off')
def block(x,y,w,h,title,body,color):
    ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle='round,pad=.07,rounding_size=.10',fc=color,ec='#c7d5df',lw=.8))
    ax.text(x+w/2,y+h*.75,title,ha='center',va='center',fontsize=11,weight='bold',color=INK)
    ax.text(x+w/2,y+h*.33,body,ha='center',va='center',fontsize=9.5,color=INK,linespacing=1.6)
block(3.25,3.55,8.5,.85,'GDS几何 + 手动用途与层序 + 工艺 + 各DIE最大偏移','修复 / Merge → 连通归属 → 真实旋转中心 → 整孔接口','#f0f5f8')
for i,(title,body,output,color) in enumerate([
 ('空间初筛 U','线路需求 / 可用空间\n按三个方案分别计算','初筛推荐与理由','#dceaf2'),
 ('每接口接触概率','完整截断分布积分\n与目标级别比较','超限 / 通过 / 待细化','#e2eee5'),
 ('连续安全内盒','分别调整X / Y / O\n验证盒内所有组合','连续安全范围','#e8e3ef'),
 ('全输入模型良率','同DIE所有接口联合\n抽样或确定性积分','估计值 + 精度区间','#f5eadc'),
 ('指定状态预演','孔盘 / 三段 / 迭代\n最终布局DRC复核','合格型变 / 保留原图','#e3eef0')]):
    x=.15+i*3
    ax.add_patch(FancyArrowPatch((7.5,3.45),(x+1.3,2.85),arrowstyle='-|>',mutation_scale=10,lw=.85,color='#a0b2bf'))
    block(x,1.25,2.6,1.5,title,body,color);ax.text(x+1.3,.85,output,ha='center',va='center',color=INK,fontsize=9.5)
ax.text(7.5,.22,'安全盒之外仍参与良率积分  ·  同DIE各孔共享姿态  ·  全输入概率仅含原始固定RDL接口接触',ha='center',color='#6b818e',fontsize=9.5)
save(fig,'V16_flow')
print('V16_FIGURES_PASS')
