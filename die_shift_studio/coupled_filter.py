"""Evaluate changed copper where independent original-geometry rules do not apply."""
import math
import numpy as np
from .edge_clearance import edge_violations
from .extreme_filter import ProblemIndex
from .width_summary import strongest
from .width_analysis import BLOCKED


def scan(engine,grid,settings,width,focused,progress):
    base=engine.width_summary(progress) if width else None
    channels=ProblemIndex(grid,base['channel_factors']) if base else None
    lines=dict(base['channel_lines']) if base else {}
    whole=width or focused is None
    visits=range(-1,grid.total) if whole else (-1,*focused)
    count=grid.total+1 if whole else len(visits)
    red=[];zero=np.zeros((grid.count,3));binary=engine.p.target_nsigma in (0.,math.inf)
    for n,index in enumerate(visits):
        title=f'逐姿态核实型变后'+('宽度' if width else '接触')+f' {n+1}/{count}'
        update=lambda *_:progress(round(100*n/count),title)
        update()
        digits=grid.choices(index) if index>=0 else [0]*grid.count
        z=np.asarray([v[k] for v,k in zip(grid.locals,digits)]) if index>=0 else zero
        current=engine.contact.current(z,engine.p)
        deformation=engine.deformer.frame(z,current,update) if engine.deformer else None
        replacements=(deformation or {}).get('replacements',{})
        if not width:
            if replacements:current=engine.contact.current(z,engine.p,replacements)
            problem=bool(current['issues'])
        else:
            reconstruction=engine.reconstructor.frame(z,deformation,update) if getattr(engine,'reconstructor',None) else None
            rebuilt=(reconstruction or {}).get('replacements',{})
            edges=edge_violations(engine.contact,z,engine.p,settings,engine.final_copper(replacements,rebuilt))
            problem=bool(edges) or channels.matches(digits)
            for row in edges:
                layer=row['layer'];i=row['indices'][0];node=engine.contact.nodes[engine.contact.ids[layer,i]]
                event=None
                if not (replacements or rebuilt) and engine.width_analyzer.probability:
                    event=engine.width_analyzer.probability.edge(layer,i,node['poly'],node['owner'],row['required_um'])
                strongest(lines,[(layer,j) for j in row['indices']],BLOCKED,event,binary)
        if problem and index>=0:red.append(index)
    return dict(lines=lines,indices=red,complete=True,package_states=grid.total+1,scanned=count)
