"""Vector and 300 dpi evidence for pivots and finite-support probability."""
from pathlib import Path
import sys,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import Polygon as Patch
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.probability_overlay import probability_maps,coverage_envelope
from die_shift_studio.settings import ViewSettings
from die_shift_studio.preview import rigid_transform
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,'axes.spines.top':False,'axes.spines.right':False,'svg.fonttype':'none'})
fig,axes=plt.subplots(1,3,figsize=(12.4,4.1),layout='constrained');blue='#548cad';rose='#b96685';green='#438f86'
a=axes[0];die=np.array([[-400,-800],[400,-800],[400,800],[-400,800]])
a.add_patch(Patch(die,facecolor='#e8f1f4',edgecolor='#9dadbb',lw=1))
point=np.array([400,800]);theta=np.deg2rad(1)
for pivot,color,title in [(np.array([0.,0.]),blue,'Centre'),(np.array([-400.,-800.]),rose,'Lower left')]:
    moved=rigid_transform(die,pivot,[0,0,theta]);a.plot(*np.vstack((moved,moved[0])).T,color=color,lw=1.2);a.scatter(*pivot,s=24,c=color,zorder=4);a.plot([pivot[0],point[0]],[pivot[1],point[1]],ls='--',color=color,lw=.8,label=title)
a.set(xlim=(-650,650),ylim=(-1000,1000),xlabel='x / μm',ylabel='y / μm',title='a  One rigid pose, different origins');a.set_aspect('equal');a.legend(frameon=False,loc='upper left',fontsize=8)
a=axes[1];radius=np.linspace(0,12000,250);a.plot(radius,2*radius*np.sin(theta/2),color=blue,lw=1.8)
a.fill_between(radius,0,2*radius*np.sin(theta/2),color='#e5f1f5');a.set(xlabel='Distance to rotation origin / μm',ylabel='Rotation displacement / μm',title='b  Exact lever-arm effect (1°)',xlim=(0,12000),ylim=(0,220));a.text(.05,.87,r'$|\Delta P|=2r\sin(|\theta|/2)$',transform=a.transAxes);a.grid(alpha=.15)
q=json.loads((ROOT/'examples/示例I_旋转中心与全范围概率.json').read_text());d=read_gds(ROOT/'examples'/q['gds_path']);c=build_case(d,q['roles'],layer_order=q['layer_order']);p=Parameters.from_dict(q['parameters']);e=PreviewEngine(GeometryCache(d),c,q['roles'],{},p,{},c['connections'])
prob=probability_maps(e.bound.cache,e.bound,q['roles'],p,masks=e.contact.interface_masks,settings=ViewSettings(probability_method='quadrature',quadrature_order=49,probability_show_ranges=True))['maps'][0]
a=axes[2];a.set_aspect('equal');im=a.imshow(np.ma.masked_equal(prob['probability'],0),extent=prob['extent'],origin='lower',cmap=LinearSegmentedColormap.from_list('cloud',['#f5fafb','#b5d6e0','#418ca4']),vmin=0,vmax=1)
for level,color in [(1,blue),(3,green),(6,rose)]:
    hull=coverage_envelope(prob['nominal_polygon'],prob['rotation_center'],p.for_die(prob['owner']),2,level);a.plot(*np.asarray(hull.exterior.coords).T,color=color,ls='--',lw=1.1,label=f'{level}σ envelope')
a.set(xlabel='x / μm',ylabel='y / μm',title='c  Full via occupancy + nested ranges');a.legend(frameon=False,fontsize=7.8,loc='upper right');fig.colorbar(im,ax=a,location='bottom',shrink=.82,pad=.14,label='Via occupancy probability')
for ext in ('png','svg'):fig.savefig(ROOT/'figures'/('V14_pivots_probability.'+ext),dpi=300)
plt.close(fig);e.close()
