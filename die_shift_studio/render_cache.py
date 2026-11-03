"""Cache axes/ticks for unchanged-view previews, preserving normal exports.

All geometry and overlays retain normal Matplotlib artists. Only the background
is cached; visibility, stacking, current geometry and alpha are rendered anew.
"""
import numpy as np
from collections import OrderedDict
from matplotlib.backends.backend_agg import RendererAgg

class SceneRenderer:
    def __init__(self,canvas,ax):
        self.canvas=canvas;self.ax=ax;self.key=None;self.background=None;self.capturing=False;self.legend_region=None;self.static_cache=OrderedDict();self.pending_key=None
        self.static_budget=64*1024**2;self.selection_background=None;self.selection_images=OrderedDict();self.unselected_key=None
        canvas.mpl_connect('draw_event',self.external_draw)
    def external_draw(self,event):
        if not self.capturing:self.key=None;self.background=None;self.selection_background=None;self.selection_images.clear();self.unselected_key=None

    def selection_key(self,legend):
        return (tuple(self.ax.get_xlim()),tuple(self.ax.get_ylim()),tuple(self.ax.bbox.bounds),self.canvas.figure.dpi,
                id(legend),bool(legend and legend.get_visible()))

    def draw_selection(self,artists,legend,signature):
        """A stable scene needs only the selected outlines and their callouts repainted."""
        key=self.selection_key(legend)
        if self.selection_background is None or self.selection_background[0]!=key:
            visible=[a for a in artists if a is not None and a.get_visible()]
            self.capturing=True;show_legend=bool(legend and legend.get_visible())
            try:
                self.selection_images.clear()
                # A freshly drawn, unselected zero view already is the base.
                # Copy that framebuffer before painting the first selection.
                if self.unselected_key!=key:
                    for a in visible:a.set_visible(False)
                    if show_legend:legend.set_visible(False)
                    self.canvas.draw()
                region=None
                if show_legend:
                    if self.unselected_key!=key:legend.set_visible(True);self.ax.draw_artist(legend)
                    bounds=legend.get_window_extent(self.canvas.get_renderer()).expanded(1.01,1.02)
                    region=self.canvas.copy_from_bbox(bounds)
                background=self.canvas.copy_from_bbox(self.ax.bbox)
                self.selection_background=(key,background,region);self.unselected_key=None
            finally:
                self.capturing=False
                for a in visible:a.set_visible(True)
                if show_legend:legend.set_visible(True)
        if signature in self.selection_images:
            self.canvas.restore_region(self.selection_images[signature]);self.selection_images.move_to_end(signature)
            self.canvas.blit(self.ax.bbox);return
        _,background,region=self.selection_background
        self.canvas.restore_region(background)
        for a in sorted((a for a in artists if a is not None and a.get_visible()),key=lambda obj:obj.get_zorder()):
            self.ax.draw_artist(a)
        if region is not None:self.canvas.restore_region(region)
        pixels=max(1,int(np.ceil(self.ax.bbox.width)*np.ceil(self.ax.bbox.height)*4))
        limit=min(3,(16*1024**2)//pixels)
        if limit:
            self.selection_images[signature]=self.canvas.copy_from_bbox(self.ax.bbox)
            while len(self.selection_images)>limit:self.selection_images.popitem(last=False)
        self.canvas.blit(self.ax.bbox)

    def draw(self,key,legend=None):
        self.selection_background=None;self.selection_images.clear();self.unselected_key=None
        artists=[a for a in list(self.ax.collections)+list(self.ax.patches)+list(self.ax.images)+list(self.ax.texts) if a.get_visible()]
        # During camera motion, the view changes every frame. A single normal
        # draw is cheaper than building background and static-run caches that
        # immediately expire. Build them only on the second stable-view draw.
        if key!=self.key and key!=self.pending_key:
            self.capturing=True
            try:self.canvas.draw()
            finally:self.capturing=False
            self.pending_key=key;self.key=None;self.background=None;self.static_cache.clear();return
        if key!=self.key or self.background is None:
            for a in artists:a.set_visible(False)
            self.capturing=True
            try:
                self.canvas.draw();self.background=self.canvas.copy_from_bbox(self.ax.bbox)
                self.legend_region=None
                if legend is not None and legend.get_visible():
                    bounds=legend.get_window_extent(self.canvas.get_renderer()).expanded(1.01,1.02)
                    self.legend_region=self.canvas.copy_from_bbox(bounds)
                self.key=key
            finally:
                self.capturing=False
                for a in artists:a.set_visible(True)
        self.canvas.restore_region(self.background)
        ordered=sorted(artists,key=lambda a:a.get_zorder());i=0
        while i<len(ordered):
            a=ordered[i]
            if not hasattr(a,'_static_preview'):
                self.ax.draw_artist(a);i+=1;continue
            run=[]
            while i<len(ordered) and hasattr(ordered[i],'_static_preview'):run.append(ordered[i]);i+=1
            cache_key=(tuple(self.ax.get_xlim()),tuple(self.ax.get_ylim()),tuple(self.ax.bbox.bounds),
                       tuple((id(x),x._static_preview) for x in run),self.canvas.figure.dpi)
            if cache_key not in self.static_cache:
                renderer=self.canvas.get_renderer();off=RendererAgg(renderer.width,renderer.height,renderer.dpi)
                for artist in run:artist.draw(off)
                # Cache only the plot rectangle, not the whole canvas.
                rgba=np.asarray(off.buffer_rgba());x0,y0,x1,y1=map(int,self.ax.bbox.extents);h=rgba.shape[0]
                self.static_cache[cache_key]=(x0,y0,rgba[h-y1:h-y0,x0:x1].copy()[::-1])
            self.static_cache.move_to_end(cache_key)
            x,y,rgba=self.static_cache[cache_key];renderer=self.canvas.get_renderer();gc=renderer.new_gc()
            gc.set_clip_rectangle(self.ax.bbox);renderer.draw_image(gc,x,y,rgba);gc.restore()
        # Revisiting an animation pose reuses its resampled overlays. Bound RAM;
        # numerical frame/overlay caches remain independently managed on disk.
        size=sum(v[2].nbytes for v in self.static_cache.values())
        while size>self.static_budget and len(self.static_cache)>1:
            _,old=self.static_cache.popitem(last=False);size-=old[2].nbytes
        if self.legend_region is not None:self.canvas.restore_region(self.legend_region)
        self.canvas.blit(self.ax.bbox)
