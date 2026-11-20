"""Batched current-rule artwork; shares its exact location list with the table."""
import numpy as np
from matplotlib.collections import PatchCollection,LineCollection
from matplotlib.colors import to_rgb,to_rgba


class DRCOverlay:
    def __init__(self,w):self.w=w;self.area=self.points=self.links=None
    def draw(self):
        w=self.w
        if self.area is None or self.area.axes is not w.ax or self.area not in w.ax.collections:
            self.area=PatchCollection([],linewidths=.65,zorder=10.2);w.ax.add_collection(self.area)
            self.points=w.ax.scatter([],[],facecolors='none',linewidths=.9,zorder=10.7)
            self.links=LineCollection([],colors='#9299a0',linestyles=(0,(3,3)),linewidths=.7,alpha=.55,zorder=10.1);w.ax.add_collection(self.links)
        show=w.show_issues.isChecked();visible={k for k,box in w.visible_boxes.items() if box.isChecked()}
        x0,y0,x1,y1=w.view_bounds;rows=[]
        for row in getattr(w,'display_issues',w.filtered_issues):
            layers=row.get('layers',(row['layer'],))
            if layers and not visible.intersection(layers):continue
            a,b,c,d=row['geometry'].bounds
            if c>=x0 and a<=x1 and d>=y0 and b<=y1:rows.append(row)
        low_prob=bool(w.reconstruction_toggle.isChecked() and hasattr(w,'is_low_probability_pose') and w.is_low_probability_pose()[0])
        base_color=w.view_settings.contact_marker_color
        if low_prob:
            muted=tuple(np.asarray(to_rgb(base_color))*.45+np.asarray(to_rgb('#9aa3ad'))*.55)
            self.area.set_paths([w.geometry_patch(r['geometry']) for r in rows if r['geometry'].geom_type in ('Polygon','MultiPolygon')])
            self.area.set_facecolor(to_rgba(muted,.10));self.area.set_edgecolor(to_rgba(muted,.32));self.area.set_linestyle('--')
            self.points.set_offsets(np.asarray([r['point'] for r in rows]).reshape(-1,2));self.points.set_sizes([w.marker_size('contact')])
            self.points.set_edgecolors(to_rgba(muted,.38));self.points.set_linewidths(.65);self.points.set_linestyle('--')
            self.links.set_segments([[r['origin'],r['point']] for r in rows if r.get('origin') is not None]);self.links.set_alpha(.20)
        else:
            self.area.set_paths([w.geometry_patch(r['geometry']) for r in rows if r['geometry'].geom_type in ('Polygon','MultiPolygon')])
            self.area.set_facecolor(to_rgba(base_color,min(.35,w.view_settings.intrusion_alpha*.45)));self.area.set_edgecolor(to_rgba(base_color,.65));self.area.set_linestyle('-')
            self.points.set_offsets(np.asarray([r['point'] for r in rows]).reshape(-1,2));self.points.set_sizes([w.marker_size('contact')])
            self.points.set_edgecolors(to_rgba(base_color,.85));self.points.set_linewidths(.9);self.points.set_linestyle('-')
            self.links.set_segments([[r['origin'],r['point']] for r in rows if r.get('origin') is not None]);self.links.set_alpha(.55)
        for artist in (self.area,self.points,self.links):artist.set_visible(show and bool(rows))
        w.rendered_issues=rows if show else []
