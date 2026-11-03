"""Indexed, owner-aware picking and CAD-style directional box selection."""
import numpy as np
import shapely
from shapely.geometry import Point,box
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
        self.w=window;self.selected=[];self.index_stamp=None;self.groups={};self.press=None;self.last_pixel=None;self.cycle=[];self.cycle_index=-1
        self.artist=None;self.deformed_artist=None;self.rubber=None;self.background=None
        for name,fn in [('button_press_event',self.down),('motion_notify_event',self.move),('button_release_event',self.up)]:window.canvas.mpl_connect(name,fn)
    def clear(self):
        self.selected=[];self.cycle=[];self.last_pixel=None;self.press=None
        if self.rubber is not None:
            self.rubber.remove();self.rubber=None
            if self.background is not None:self.w.canvas.restore_region(self.background);self.background=None
        self.draw()
    def reset(self):
        self.index_stamp=None;self.groups={};self.last_pixel=None;self.selected=[];self.artist=None;self.deformed_artist=None;self.rubber=None;self.press=None;self.cycle=[]
    def indexed(self):
        w=self.w
        if not w.data:return
        stamp=(id(w.data),id(w.case))
        if stamp==self.index_stamp:return
        # Picking and drawing must refer to the same source IDs and owner geometry.
        viewport=getattr(w,'viewport_index',None)
        if viewport is None or (getattr(w,'_viewport_stamp',None) or ())[:2]!=stamp:
            from .viewport import ViewportIndex
            from .fast_preview import GeometryCache
            w.geometry_cache=w.geometry_cache or GeometryCache(w.data)
            viewport=ViewportIndex(w.geometry_cache,w.case,w.motions(),w.params())
        self.groups=viewport.groups;self.index_stamp=stamp

    def replacements(self):
        w=self.w
        return w.current_frame.get('deformation',{}).get('replacements',{}) if w.current_frame and w.deformation_toggle.isChecked() else {}

    def hits(self,shape,contained=False,point=False):
        w=self.w
        if not w.data:return []
        self.indexed();found=[];roles=w.roles()
        replacements=self.replacements()
        hidden={(k,i) for k,items in replacements.items() for q in items for i in q['indices']}
        summary=w.width_overlay.shown_summary() if hasattr(w,'width_overlay') else None
        marked=summary['lines'] if summary and w.view_settings.width_display['描边']!='hide' else {}
        locals_by_owner={};centers=w.rotation_centers() if w.shown_states is not None else None
        order={k:i for i,k in enumerate(w.ordered_layers())}
        for key,groups in self.groups.items():
            if not w.visible_boxes[key].isChecked() or roles.get(key) in ('芯片边框','封装边框'):continue
            only_marked=key in getattr(w,'width_hidden_sources',())
            if only_marked and not marked:continue
            filled=only_marked or not point or w.style_for(key)['fill']
            for owner,ids,tree in groups:
                if owner not in locals_by_owner:
                    local=inverse_geometry(shape,centers[owner],w.shown_states[owner]) if centers is not None and owner>=0 else shape
                    locals_by_owner[owner]=(local,local.centroid if point else None)
                local,center=locals_by_owner[owner]
                probe=center if point and filled else local
                matches=tree.query(probe,predicate='covers' if contained else 'intersects')
                if point and not filled and len(matches):
                    geos=tree.geometries[matches]
                    hit=shapely.intersects(local,shapely.boundary(geos))
                    if marked:
                        tinted=np.fromiter(((key,i) in marked for i in ids[matches]),bool)
                        hit[tinted] |= shapely.intersects(center,geos[tinted])
                    matches=matches[hit]
                found.extend((key,int(i)) for i in ids[matches] if (key,i) not in hidden and (not only_marked or (key,i) in marked))
            for item in replacements.get(key,()):
                poly=item['geometry']
                hit=poly.covers(shape.centroid) if point and filled else shape.intersects(poly.boundary) if point else shape.covers(poly) if contained else shape.intersects(poly)
                ref=(key,item['indices'][0])
                if hit and (not only_marked or ref in marked):found.append(ref)
        return sorted(found,key=lambda ref:(w.layer_artists[ref[0]].get_zorder(),order[ref[0]],ref[1]),reverse=True)

    def apply_hits(self,hits,mode):
        if mode=='add':
            self.selected=list(dict.fromkeys((*self.selected,*hits)))
        elif mode=='subtract':
            remove=set(hits)
            self.selected=[item for item in self.selected if item not in remove]
        else:self.selected=list(hits)
        self.draw()

    def click(self,point,pixel,mode='replace'):
        w=self.w;tol=np.ptp(w.ax.get_xlim())/max(1,w.ax.bbox.width)*3
        hits=self.hits(Point(point).buffer(tol),point=True)
        if mode=='replace':
            repeat=self.last_pixel is not None and np.linalg.norm(np.asarray(pixel)-self.last_pixel)<=4 and hits==self.cycle
            self.cycle_index=(self.cycle_index+1)%len(hits) if repeat and hits else 0
            self.cycle=hits;self.last_pixel=np.asarray(pixel)
            target=hits[self.cycle_index:self.cycle_index+1]
        else:
            self.last_pixel=None;selected=set(self.selected)
            target=next(([hit] for hit in hits if (hit in selected)==(mode=='subtract')),[])
        self.apply_hits(target,mode)

    def rectangle(self,start,end,mode='replace'):
        bounds=(*np.minimum(start,end),*np.maximum(start,end))
        self.last_pixel=None
        self.apply_hits(self.hits(box(*bounds),contained=end[0]>=start[0]),mode)

    def down(self,event):
        if event.button!=1 or event.inaxes is not self.w.ax or self.w.navigation.busy():return
        from .navigation import pointer_modifiers
        modifiers=pointer_modifiers(event)
        mode='subtract' if 'ctrl' in modifiers else 'add' if 'shift' in modifiers else 'replace'
        self.w.stop_animation();self.press=(np.array([event.x,event.y]),np.array([event.xdata,event.ydata]),mode);self.background=None
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
        pixel,start,mode=self.press;self.press=None
        if self.rubber is not None:
            self.rubber.remove();self.rubber=None
            if self.background is not None:self.w.canvas.restore_region(self.background);self.background=None
        end=self.w.ax.transData.inverted().transform((event.x,event.y))
        if np.linalg.norm(np.array([event.x,event.y])-pixel)<4:self.click(end,[event.x,event.y],mode)
        else:self.rectangle(start,end,mode)
    def draw(self,redraw=True):
        w=self.w;replacements=self.replacements();hidden={(k,i) for k,items in replacements.items() for q in items for i in q['indices']}
        refs=set(self.selected);roles=w.roles() if refs else {}
        polys=[]
        if refs:
            by_layer={}
            for k,i in self.selected:
                if (k,i) not in hidden:by_layer.setdefault(k,[]).append(i)
            for key,ids in by_layer.items():
                if key not in w.rendered_layers or roles.get(key) in ('芯片边框','封装边框'):continue
                shapes=w.rendered_layers[key]
                visible=getattr(w.layer_artists[key],'_visible_ids',None)
                if visible is not None and len(visible)//np.dtype(int).itemsize<len(ids):
                    ids=(i for i in np.frombuffer(visible,dtype=int) if (key,i) in refs and (key,i) not in hidden)
                polys.extend(shapes[i] for i in ids if i<len(shapes))
        if self.artist is None:
            self.artist=PolyCollection([],facecolors=(.13,.60,.88,.18),edgecolors='#1769ac',linewidths=1.5,zorder=14);w.ax.add_collection(self.artist)
        self.artist.set_verts(polys)
        if self.deformed_artist is None:
            self.deformed_artist=PatchCollection([],facecolors=(.13,.60,.88,.18),edgecolors='#1769ac',linewidths=1.5,zorder=14);w.ax.add_collection(self.deformed_artist)
        paths=[PathPatch(geometry_path(q['geometry'])) for k,items in replacements.items() if w.visible_boxes[k].isChecked()
               and k not in getattr(w,'width_hidden_sources',()) for q in items if any((k,i) in refs for i in q['indices'])]
        hidden_sources=getattr(w,'width_hidden_sources',())
        if refs and hidden_sources and w.view_settings.width_display['描边']!='hide':
            summary=w.width_overlay.shown_summary()
            for k,i in refs:
                if k in hidden_sources and w.visible_boxes[k].isChecked() and (k,i) in (summary or {}).get('lines',{}):
                    paths.append(w.geometry_patch(w.geometry_cache.layers[k]['geometry'][i]))
        self.deformed_artist.set_paths(paths)
        if redraw:
            summary=w.width_overlay.shown_summary() if hasattr(w,'width_overlay') else None
            if self.selected:w.statusBar().showMessage(f'选中 {len(self.selected)} 个图形；'+', '.join(f'{k} #{i+1}' for k,i in self.selected[:6]))
            else:w.statusBar().showMessage('已取消图形选择')
            if w.tabs.currentIndex()!=0:return
            artists=[self.artist,self.deformed_artist]
            if summary is not None:
                w.width_overlay.draw();artists.extend(w.width_overlay.labels)
            signature=(tuple(self.selected),w.width_overlay.stamp if summary is not None else None)
            w.scene_renderer.draw_selection(artists,w.legend,signature)
