"""Indexed, owner-aware picking and CAD-style directional box selection."""
import numpy as np
import shapely
from shapely.geometry import Point,box
from shapely.strtree import STRtree
from shapely.affinity import affine_transform
from matplotlib.collections import PolyCollection,PatchCollection
from matplotlib.patches import PathPatch
from .plot_geometry import geometry_path
from matplotlib.patches import Rectangle


def inverse_geometry(shape,center,state):
    c,s=np.cos(state[2]),np.sin(state[2]);x,y=np.asarray(center)+state[:2]
    return affine_transform(shape,[c,s,-s,c,center[0]-c*x-s*y,center[1]+s*x-c*y])

class GeometrySelection:
    def __init__(self,window):
        self.w=window;self.selected=[];self.index_stamp=None;self.groups=[];self.press=None;self.last_pixel=None;self.cycle=[];self.cycle_index=-1
        self.artist=None;self.deformed_artist=None;self.rubber=None;self.background=None
        for name,fn in [('button_press_event',self.down),('motion_notify_event',self.move),('button_release_event',self.up)]:window.canvas.mpl_connect(name,fn)
    def clear(self):
        self.selected=[];self.cycle=[];self.last_pixel=None;self.press=None
        if self.rubber is not None:self.rubber.remove();self.rubber=None
        self.draw()
    def reset(self):
        self.index_stamp=None;self.selected=[];self.artist=None;self.deformed_artist=None;self.rubber=None;self.press=None;self.cycle=[]
    def indexed(self):
        w=self.w
        if not w.data:return
        stamp=(id(w.data),id(w.case))
        if stamp==self.index_stamp:return
        from .fast_preview import GeometryCache
        cache=w.geometry_cache or GeometryCache(w.data);w.geometry_cache=cache
        bound=cache.bind(w.case,w.motions(),cache.layers,w.params()) if w.case else None
        self.groups=[]
        for key,q in cache.layers.items():
            owners=bound.owners[key] if bound else np.full(len(q['geometry']),-1)
            for owner in np.unique(owners):
                ids=np.flatnonzero(owners==owner);self.groups.append((key,int(owner),ids,STRtree(q['geometry'][ids])))
        self.index_stamp=stamp
    def replacements(self):
        w=self.w
        return w.current_frame.get('deformation',{}).get('replacements',{}) if w.current_frame and w.deformation_toggle.isChecked() else {}
    def hits(self,shape,contained=False,point=False):
        self.indexed();w=self.w;found=[];replacements=self.replacements();hidden={(k,i) for k,items in replacements.items() for q in items for i in q['indices']}
        for key,owner,ids,tree in self.groups:
            if not w.visible_boxes[key].isChecked() or w.roles().get(key) in ('芯片边框','封装边框'):continue
            local=shape
            if owner>=0 and w.shown_states is not None:
                local=inverse_geometry(shape,w.rotation_centers()[owner],w.shown_states[owner])
            candidates=tree.query(local,predicate='intersects')
            for j in candidates:
                ref=(key,int(ids[j]))
                keep=getattr(w,'width_source_refs',None)
                if ref in hidden or (keep is not None and w.roles().get(key)=='固定RDL' and ref not in keep):continue
                poly=tree.geometries[j]
                if contained and not local.covers(poly):continue
                # Unfilled outlines are selectable at the outline, never across their interior.
                if point:
                    if w.style_for(key)['fill']:
                        if not poly.covers(local.centroid):continue
                    elif not local.intersects(poly.boundary):continue
                found.append((key,int(ids[j])))
        for key,items in replacements.items():
            if not w.visible_boxes[key].isChecked():continue
            for q in items:
                keep=getattr(w,'width_source_refs',None)
                if keep is not None and not any((key,i) in keep for i in q['indices']):continue
                poly=q['geometry']
                hit=poly.covers(shape.centroid) if point else shape.covers(poly) if contained else shape.intersects(poly)
                if hit:found.append((key,q['indices'][0]))
        return sorted(found,key=lambda a:(w.layer_artists[a[0]].get_zorder(),w.ordered_layers().index(a[0]),a[1]),reverse=True)
    def click(self,point,pixel):
        w=self.w;tol=np.ptp(w.ax.get_xlim())/max(1,w.ax.bbox.width)*3
        hits=self.hits(Point(point).buffer(tol),point=True)
        repeat=self.last_pixel is not None and np.linalg.norm(np.asarray(pixel)-self.last_pixel)<=4 and hits==self.cycle
        self.cycle_index=(self.cycle_index+1)%len(hits) if repeat and hits else 0
        self.cycle=hits;self.last_pixel=np.asarray(pixel);self.selected=[hits[self.cycle_index]] if hits else [];self.draw()
    def rectangle(self,start,end):
        self.selected=self.hits(box(min(start[0],end[0]),min(start[1],end[1]),max(start[0],end[0]),max(start[1],end[1])),contained=end[0]>=start[0]);self.last_pixel=None;self.draw()
    def down(self,event):
        if event.button!=1 or event.inaxes is not self.w.ax or self.w.navigation.busy():return
        self.w.stop_animation();self.press=(np.array([event.x,event.y]),np.array([event.xdata,event.ydata]));self.background=None
    def move(self,event):
        if self.press is None or event.xdata is None or event.inaxes is not self.w.ax:return
        if np.linalg.norm(np.array([event.x,event.y])-self.press[0])<4:return
        if self.rubber is None:
            self.rubber=Rectangle((0,0),0,0,facecolor='#629fc2',alpha=.18,edgecolor='#1f638f',linewidth=1,zorder=30,animated=True);self.w.ax.add_patch(self.rubber)
            self.background=self.w.canvas.copy_from_bbox(self.w.ax.bbox)
        start=self.press[1];end=np.array([event.xdata,event.ydata]);self.rubber.set_bounds(*np.minimum(start,end),*np.abs(end-start));self.rubber.set_linestyle('-' if end[0]>=start[0] else '--')
        self.w.canvas.restore_region(self.background);self.w.ax.draw_artist(self.rubber);self.w.canvas.blit(self.w.ax.bbox)
    def up(self,event):
        if event.button!=1 or self.press is None:return
        pixel,start=self.press;self.press=None
        if self.rubber is not None:self.rubber.remove();self.rubber=None
        end=self.w.ax.transData.inverted().transform((event.x,event.y))
        if np.linalg.norm(np.array([event.x,event.y])-pixel)<4:self.click(end,[event.x,event.y])
        else:self.rectangle(start,end)
    def draw(self,redraw=True):
        w=self.w;replacements=self.replacements();hidden={(k,i) for k,items in replacements.items() for q in items for i in q['indices']}
        polys=[w.rendered_layers[k][i] for k,i in self.selected if (k,i) not in hidden and (getattr(w,'width_source_refs',None) is None or w.roles().get(k)!='固定RDL' or (k,i) in w.width_source_refs) and k in w.rendered_layers and i<len(w.rendered_layers[k]) and w.roles().get(k) not in ('芯片边框','封装边框')]
        if self.artist is None:
            self.artist=PolyCollection([],facecolors=(.13,.60,.88,.18),edgecolors='#1769ac',linewidths=1.5,zorder=14);w.ax.add_collection(self.artist)
        self.artist.set_verts(polys)
        if self.deformed_artist is None:
            self.deformed_artist=PatchCollection([],facecolors=(.13,.60,.88,.18),edgecolors='#1769ac',linewidths=1.5,zorder=14);w.ax.add_collection(self.deformed_artist)
        self.deformed_artist.set_paths([PathPatch(geometry_path(q['geometry'])) for k,items in replacements.items() if w.visible_boxes[k].isChecked() for q in items if any((k,i) in self.selected and (getattr(w,'width_source_refs',None) is None or (k,i) in w.width_source_refs) for i in q['indices'])])
        if redraw:
            if self.selected:w.statusBar().showMessage(f'选中 {len(self.selected)} 个图形；'+', '.join(f'{k} #{i+1}' for k,i in self.selected[:6]))
            else:w.statusBar().showMessage('已取消图形选择')
            w.render_scene()
