"""Failure connections are paired dashed edges, never added copper."""
from matplotlib.collections import LineCollection

def draw(w):
    actor=getattr(w,'failed_connections_artist',None)
    if actor is None or actor not in w.ax.collections:
        actor=LineCollection([],colors='#c52e38',linewidths=1.2,linestyles=(0,(5,3)),alpha=.85,zorder=14)
        w.ax.add_collection(actor,autolim=False);w.failed_connections_artist=actor
    rows=(w.current_frame or {}).get('reconstruction',{}).get('failed_segments',())
    actor.set_segments([r['points'] for r in rows if w.reconstruction_toggle.isChecked() and r['layer'] in w.visible_boxes and w.visible_boxes[r['layer']].isChecked()])
    actor.set_visible(bool(rows) and w.reconstruction_toggle.isChecked())
