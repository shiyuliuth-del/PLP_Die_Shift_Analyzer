"""Publication-style original figures. Save vector SVG + 300 dpi PNG."""
from pathlib import Path
import sys,json,math
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle,Circle,Ellipse,FancyArrowPatch,Arc
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.ticker import ScalarFormatter
from scipy.stats import chi2,norm

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'figures';OUT.mkdir(exist_ok=True)
plt.rcParams.update({'font.family':['Noto Sans CJK JP','DejaVu Sans'],'font.size':10,
 'axes.labelsize':10,'axes.titlesize':11,'axes.linewidth':.65,'xtick.labelsize':9,
 'ytick.labelsize':9,'axes.unicode_minus':False,'svg.fonttype':'none',
 'savefig.facecolor':'white','figure.facecolor':'white'})
INK='#354858';BLUE='#8eafc7';GREEN='#a9c9b6';PURPLE='#c6b4d4';PEACH='#dfc3ae';LIGHT='#eff3f6'

def save(fig,name):
    fig.savefig(OUT/(name+'.png'),dpi=300,bbox_inches='tight',pad_inches=.12)
    fig.savefig(OUT/(name+'.svg'),bbox_inches='tight',pad_inches=.12)
    vector=OUT/(name+'.svg');vector.write_text('\n'.join(line.rstrip() for line in vector.read_text().splitlines())+'\n')
    plt.close(fig)
def tidy(ax):
    ax.spines[['top','right']].set_visible(False);ax.grid(alpha=.13,lw=.6)
    for axis in (ax.xaxis,ax.yaxis):
        if isinstance(axis.get_major_formatter(),ScalarFormatter):
            axis.get_major_formatter().set_scientific(False)
            axis.get_major_formatter().set_useOffset(False)

def methods():
    fig,axs=plt.subplots(1,3,figsize=(12,4.4),layout='constrained')
    titles=['a  局部重构','b  整体随动','c  晶圆段预制 RDL']
    subtitles=['动态通道吸收偏移','固定过孔端孔盘吸收偏移','重新安排动静接口']
    for i,ax in enumerate(axs):
        ax.set(xlim=(0,10),ylim=(0,10));ax.axis('off');ax.set_title(titles[i],loc='left',fontweight='bold',color=INK,pad=10)
        ax.add_patch(Rectangle((1.2,6.8),7.0,1.8,facecolor='#e0eaf1',edgecolor=BLUE,lw=1))
        ax.text(4.7,7.7,'芯片与随动区',ha='center',va='center',color=INK)
        ax.annotate('',xy=(8.7,8.8),xytext=(7.2,8.8),arrowprops=dict(arrowstyle='->',color=INK,lw=1))
        ax.text(2.4,9.0,'ΔX、ΔY、θ',ha='center',color=INK,fontsize=9)
        for x in (2.0,3.6,5.2,6.8):
            ax.add_patch(Circle((x,1.6),.29,facecolor='#f0e3d9',edgecolor=PEACH,lw=1))
            if i==0:
                ax.plot([x,x],[1.9,3.5],color='#9babb8',lw=2)
                ax.plot([x,x+.65,x+.65],[3.5,4.8,6.8],color=BLUE,lw=2)
            elif i==1:
                ax.add_patch(Circle((x,3),.55,facecolor='#e8e0ef',edgecolor=PURPLE,lw=1))
                ax.plot([x+.35,x+.65,x+.65],[3.15,4.8,6.8],color=BLUE,lw=2)
                ax.plot([x,x],[1.9,3.0],color='#9babb8',lw=2)
            else:
                ax.plot([x,x],[1.9,3.7],color='#9babb8',lw=2)
                ax.plot([x,x+.65],[3.7,5.55],color=GREEN,lw=2)
                ax.add_patch(Circle((x+.65,5.6),.32,facecolor='#e0ece4',edgecolor=GREEN,lw=1))
                ax.plot([x+.65,x+.65],[5.9,6.8],color=BLUE,lw=2)
        if i==0:
            ax.add_patch(Rectangle((1.1,3.5),7.7,1.4,facecolor='#eef3f8',alpha=.55,edgecolor=BLUE,ls='--',lw=.8))
            ax.text(5.0,4.2,'动态连接窗口',ha='center',va='center',color=INK,fontsize=9,bbox=dict(facecolor='white',alpha=.9,edgecolor='none',pad=2))
        if i==1:ax.text(5,4.2,'扩大捕获盘',ha='center',color=INK,fontsize=9,bbox=dict(facecolor='white',edgecolor='none',alpha=.9))
        if i==2:ax.text(4.8,6.25,'晶圆段 RDL / 输出接口',ha='center',color=INK,fontsize=9,bbox=dict(facecolor='white',alpha=.9,edgecolor='none',pad=1))
        ax.text(5,.55,'固定过孔',ha='center',color=INK,fontsize=9)
        ax.text(5,-.2,subtitles[i],ha='center',color=INK,fontsize=10)
    save(fig,'01_three_schemes')

def displacement_diagram():
    fig,axs=plt.subplots(1,2,figsize=(10.8,4.2),layout='constrained')
    ax=axs[0];a=np.array([1800.,1000.]);theta=np.deg2rad(7);D=np.array([420.,260.])
    R=np.array([[np.cos(theta),-np.sin(theta)],[np.sin(theta),np.cos(theta)]]);b=R@a;actual=D+b
    ax.plot([0,a[0]],[0,a[1]],color=BLUE,lw=1.7,label='名义位置 a')
    ax.plot([D[0],actual[0]],[D[1],actual[1]],color=GREEN,lw=1.7,label='平移并旋转后的芯片坐标')
    for start,end,text,col in [(a,a+D,'平移 D',PURPLE),(a+D,actual,'旋转位移',PEACH),(a,actual,'合位移 u',INK)]:
        ax.annotate('',xy=end,xytext=start,arrowprops=dict(arrowstyle='->',color=col,lw=1.6))
        pos=(start+end)/2;ax.annotate(text,xy=pos,xytext=(4,8),textcoords='offset points',fontsize=9,color=INK)
    ax.scatter([0,D[0],a[0],actual[0]],[0,D[1],a[1],actual[1]],color=[BLUE,GREEN,BLUE,INK],s=20,zorder=5)
    ax.set(xlabel='X / μm',ylabel='Y / μm',title='a  刚体变换分解（位移放大示意）',xlim=(-200,2800),ylim=(-150,1900));ax.set_aspect('equal');tidy(ax)
    ax.legend(loc='upper left',frameon=False,fontsize=8)
    ax=axs[1];st=25/6;so=np.deg2rad(1/6);r=5000;k=np.sqrt(-2*np.log(2*norm.sf(6)))
    sr=st;stan=np.hypot(st,r*so)
    ax.add_patch(Ellipse((0,0),2*k*sr,2*k*stan,facecolor='#e5edf4',edgecolor=BLUE,lw=1.2))
    ax.axhline(0,color='#c5cdd4',lw=.6);ax.axvline(0,color='#c5cdd4',lw=.6)
    ax.annotate('',xy=(k*sr,0),xytext=(0,0),arrowprops=dict(arrowstyle='<->',color=INK))
    ax.annotate('',xy=(0,k*stan),xytext=(0,0),arrowprops=dict(arrowstyle='<->',color=INK))
    ax.text(30,8,r'径向半轴：$q_2\sigma_t$',fontsize=9,color=INK)
    ax.text(5,65,r'切向半轴：$q_2\sqrt{\sigma_t^2+r^2\sigma_\theta^2}$',fontsize=9,color=INK)
    ax.set(xlabel='径向误差 / μm',ylabel='切向误差 / μm',title='b  旋转形成切向长轴（小角度模型）',xlim=(-110,150),ylim=(-115,115));ax.set_aspect('equal');tidy(ax)
    save(fig,'02_displacement_distribution')

def sensitivity():
    L=np.linspace(400,12000,180);alpha=2*norm.sf(6);so=np.deg2rad(1/6)
    fig,axs=plt.subplots(1,2,figsize=(11.4,4.3),layout='constrained')
    for ax,M,st,title in [(axs[0],1,25/6,'a  单芯片'),(axs[1],4,35/6,'b  四芯片整包（每颗同尺寸）')]:
        k=np.sqrt(chi2.isf(alpha/M,3))
        for ratio,col in [(1,BLUE),(2,GREEN),(3,PURPLE)]:
            H=L/ratio;r=np.hypot(L,H)/2;q=k*np.hypot(st,r*so)+.5*r*(k*so)**2
            nominal=(H>=500)&(L<=10000)
            ax.plot(L,q,color=col,ls=':',lw=1.1)
            ax.plot(L[nominal],q[nominal],color=col,lw=2,label=f'长宽比 {ratio}:1')
        ax.axhline(70,color='#99a5af',ls='--',lw=1,label='示例净容差 70 μm')
        ax.axvspan(10000,12000,color='#f2f4f6',zorder=-2)
        ax.set(xlim=(400,12000),ylim=(0,200),xlabel='芯片长边 L / μm',ylabel='角点所需统一净容差 Q / μm',title=title)
        ax.set_xticks([500,2000,4000,6000,8000,10000,12000]);tidy(ax);ax.legend(frameon=False,fontsize=9,loc='upper left')
    fig.text(.5,-.025,'实线：长宽均在500～10000 μm；点线：范围外扩。输入±6σ，目标整包等效6σ；含旋转二阶余项。',ha='center',fontsize=9,color=INK)
    save(fig,'03_size_aspect_sensitivity')
    W,H=np.meshgrid(np.linspace(400,12000,500),np.linspace(400,12000,500));rr=np.hypot(W,H)/2
    k=np.sqrt(chi2.isf(alpha,3));Q=k*np.hypot(25/6,rr*so)+.5*rr*(k*so)**2
    mask=np.maximum(W,H)/np.minimum(W,H)>3
    Q=np.ma.array(Q,mask=mask)
    cmap=LinearSegmentedColormap.from_list('paper',['#f4f7fb','#dbe7f1','#b6cee0','#86a8c3'])
    fig,ax=plt.subplots(figsize=(6.7,5.2),layout='constrained');ax.set_facecolor('#fafafa')
    im=ax.pcolormesh(W,H,Q,cmap=cmap,shading='auto',rasterized=True,vmin=25,vmax=180)
    cs=ax.contour(W,H,Q,levels=[50,70,100,150],colors=INK,linewidths=.7);ax.clabel(cs,fmt='%g μm',fontsize=8)
    ax.add_patch(Rectangle((500,500),9500,9500,fill=False,edgecolor='#8a929a',ls='--',lw=.9))
    ax.set(xlabel='芯片宽 W / μm',ylabel='芯片高 H / μm',title='单芯片角点捕获容差：尺寸与长宽比联合影响',xlim=(400,12000),ylim=(400,12000));ax.set_aspect('equal')
    ax.set_xticks([500,3000,6000,9000,12000]);ax.set_yticks([500,3000,6000,9000,12000])
    cb=fig.colorbar(im,ax=ax,pad=.025,shrink=.87);cb.set_label('整芯片统一捕获包络 Q / μm');cb.outline.set_linewidth(.5)
    fig.text(.5,-.015,'空白区域：长宽比超过3:1；虚线框：两边均为500～10000 μm。未包含套刻/形变预留。',ha='center',fontsize=8,color=INK)
    save(fig,'04_size_heatmap')

def examples():
    names=['示例A_小芯片大孔盘','示例B_大芯片局部重构','示例C_多芯片晶圆接口']
    fig,axs=plt.subplots(1,3,figsize=(11.8,3.7),layout='constrained')
    for i,(name,ax) in enumerate(zip(names,axs)):
        r=json.loads((ROOT/'results'/f'{name}_v050.json').read_text())
        for j,(s,c) in enumerate(zip(r['schemes'],[BLUE,GREEN,PURPLE])):
            v=s['gamma'];v=float(v) if v!='Infinity' else math.inf
            ax.bar(j,min(v,4.2),color=c,width=.58)
            ax.text(j,min(v,4.2)+.12,'∞' if not math.isfinite(v) else f'{v:.2f}'+(' ↑' if v>4.2 else ''),ha='center',fontsize=9,color=INK)
            if s['status'] in ('结构不适用','资料待补充'):
                ax.text(j,.15,'不适用' if s['status']=='结构不适用' else '待补充',rotation=90,va='bottom',ha='center',color=INK,fontsize=8)
        ax.axhline(1,color='#8a99a5',ls='--',lw=1);ax.set_xticks([0,1,2],['方案1','方案2','方案3']);ax.set_ylim(0,4.8)
        outcome=f"优先方案{r['recommendation']['scheme']}" if r['recommendation']['scheme'] else '暂无候选'
        ax.set_title(f"{'abc'[i]}  示例{'ABC'[i]}：{outcome}",loc='left');ax.set_ylabel('综合筛选比 Γ');tidy(ax)
    fig.text(.5,-.035,'合成工程示例，用于复算与软件验证；不是工厂实测，也不代表已完成自动布线或DRC签核。',ha='center',fontsize=9,color=INK)
    save(fig,'05_case_comparison')

if __name__=='__main__':
    methods();displacement_diagram();sensitivity();examples();print('RESEARCH_FIGURES_PASS')
