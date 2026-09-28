"""Spawn-safe, session-scoped independent RDL searches.

Workers receive only in-memory GDS geometry/settings from the parent, never
pickle files from projects. Final simultaneous-layout acceptance stays in the
parent process. The installed KLayout backend remains available offline.
"""
import atexit
import numpy as np

_worker=None

def initialize(model,parameters,settings):
    global _worker
    from . import resources
    resources._local.nested=True
    from .deformation import DeformationPreview
    _worker=DeformationPreview(model,parameters,{},settings)
    _worker._component_lookup={(c.key,c.indices):c for c in _worker.components}
    _worker._job_snapshot_key=None
    atexit.register(_worker.close)


def search_job(payload):
    key,active_ids,states,replacements,stamp,quick=payload
    d=_worker
    if d._job_snapshot_key!=stamp:
        d._job_snapshot_key=stamp
        d._job_vias=d.prepare_pose(np.asarray(states,float))
        d._job_snapshot=d._snapshot(replacements)
    c=d._component_lookup[key]
    active=[r for r in c.records if (r['key'],r['index']) in active_ids]
    anchors=c.anchors+[r['poly'] for r in c.records if (r['key'],r['index']) not in active_ids]
    try:return d.search(c,active,anchors,states,d._job_vias,d._job_snapshot,quick=quick),''
    except (ValueError,RuntimeError) as exc:return None,str(exc)
