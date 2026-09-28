"""Connection eligibility diagram and measured performance; PNG + SVG."""
from pathlib import Path
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle,Circle
ROOT=Path(__file__).resolve().parents[1]
plt.rcParams.update({'font.family':['Noto Sans CJK JP','DejaVu Sans'],'font.size':10,
 'axes.unicode_minus':False,'axes.spines.top':False,'axes.spines.right':False,
 'text.color':'#344355','axes.labelcolor':'#344355','axes.edgecolor':'#96a3b1',
 'xtick.color':'#536173','ytick.color':'#536173','svg.fonttype':'path'})
def save(fig,name):
    for ext in ('png','svg'):fig.savefig(ROOT/'figures'/f'v040_{name}.{ext}',dpi=300,facecolor='white',bbox_inches='tight')
    plt.close(fig)
fig,axs=plt.subplots(1,3,figsize=(11.6,3.6),layout='constrained')
titles=['a  相邻下层有重叠','b  相邻下层无重叠','c  仅更下层有重叠']
notes=['纳入窗口评估','排除：没有向下连接','排除：不能跳过中间参与层']
for i,ax in enumerate(axs):
    ax.set(xlim=(0,10),ylim=(0,7));ax.axis('off');ax.set_title(titles[i],loc='left',fontweight='bold',pad=12)
    ax.add_patch(Circle((3,4.2),.48,facecolor='#24647a',edgecolor='none',zorder=3))
    ax.text(3,5.2,'上层运动过孔',ha='center',fontsize=10)
    ax.add_patch(Rectangle((1,3.2 if i==0 else 1.5),7,1.6 if i==0 else .8,facecolor='#b7cfe4',edgecolor='none',alpha=.7))
    ax.text(8.4,3.5 if i==0 else 1.9,'下层\nRDL',ha='center',va='center',fontsize=9)
    if i==2:
        ax.add_patch(Circle((3,4.2),.85,fill=False,edgecolor='#947ca9',lw=1.2,ls='--'))
        ax.text(5.6,4.2,'更下层过孔',ha='center',fontsize=9,color='#796489')
    ax.text(5,.35,notes[i],ha='center',fontsize=10,color='#447a68' if i==0 else '#7a8491')
fig.suptitle('不足位置的资格判定：先有名义连接，再检查偏移窗口',fontsize=14,fontweight='bold',y=1.07)
save(fig,'connections')
r=json.loads((ROOT/'results/performance_v040.json').read_text());t=r['timings']
fig,axs=plt.subplots(1,2,figsize=(10.2,3.9),layout='constrained')
for ax,label,old,new in [(axs[0],'a  一帧刚体变换','reference_transform','cached_transform_auto'),(axs[1],'b  方案1几何检查','reference_state_check','cached_state_check')]:
    vals=[t[old]['median_ms'],t[new]['median_ms']]
    ax.bar(['参考实现','缓存实现'],vals,color=['#c9c8da','#a8caba'],width=.55)
    for x,v in enumerate(vals):ax.text(x,v+max(vals)*.03,f'{v:.2f}',ha='center',fontsize=10)
    ax.set(title=label,ylabel='耗时中位数 / ms',ylim=(0,max(vals)*1.2));ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True)
fig.suptitle(f'同输入计算内核对比 · {r["polygons"]:,}个多边形 / {r["vertices"]:,}个顶点',fontsize=13,fontweight='bold',y=1.06)
save(fig,'performance')
print('FIGURES_PASS: nominal connections + same-input benchmark, SVG and 300 dpi PNG')
