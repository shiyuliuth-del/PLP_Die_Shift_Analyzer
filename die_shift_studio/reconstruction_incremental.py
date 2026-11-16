"""Reuse untouched source features from the last complete same-pose frame."""


def source_settings(settings,ref):
    values=vars(settings)
    names=('reconstruction_style','reconstruction_boundary_mode','reconstruction_pad_trigger_um','drc_tolerance_um','reconstruction_corner_guard_um')
    out={k:values[k] for k in names}
    for key in ('reconstruction_forced','reconstruction_ignored','reconstruction_following','reconstruction_angles','reconstruction_deleted','reconstruction_added','reconstruction_intervals','reconstruction_cuts','reconstruction_extra_cuts','reconstruction_second_cuts','reconstruction_dropped_cuts'):
        out[key]=tuple(row for row in values[key] if tuple(row[:2])==ref)
    out['regions']=tuple(row for row in values['reconstruction_regions'] if row[0]==ref[0])
    return out


def reusable(r,states,selected):
    previous=getattr(r,'predecessor',None)
    if previous is None or previous is r or previous.rules.identity!=r.rules.identity:return {},[]
    frame=getattr(previous,'_last_raw_frames',{}).get(states.tobytes())
    if frame is None:return {},[]
    refs={(layer,i) for layer,rows in frame['replacements'].items() for row in rows for i in row['indices']}
    refs={ref for ref in refs if (selected is None or ref in selected) and source_settings(r.settings,ref)==source_settings(previous.settings,ref)}
    replacements={layer:[dict(row) for row in rows if all((layer,i) in refs for i in row['indices'])] for layer,rows in frame['replacements'].items()}
    replacements={k:v for k,v in replacements.items() if v}
    pieces=[dict(q,regions=dict(q['regions']),nominal_regions=dict(q['nominal_regions'])) for q in frame['pieces'] if (q['layer'],q['index']) in refs]
    return replacements,pieces
