"""One catalogue scope for GUI assessment, reconstruction and risk summaries."""
import math
import numpy as np
from scipy.special import logsumexp
from .statistics import ExtremeCatalog
from .representatives import RepresentativeExtremes


def focused_catalog(model,p,grid=None):
    grid=grid or ExtremeCatalog(model.case,p)
    selected=RepresentativeExtremes.from_result(model.case,p,{'interdie_networks':model.interdie_networks},grid)
    return grid,(-1,*selected.indices)


def digits_for(grid,index):return grid.choices(index) if index>=0 else [0]*grid.count


def conditional_weights(grid,p,indices):
    """Assign bounded-normal mass to local midpoint cells, then condition on S.

    These are representative-cell weights, not probabilities of isolated points
    of a continuous distribution. All scopes include the physical zero pose.
    """
    from .reconstruction_probability import extreme_weights
    local,error=extreme_weights(grid,p);logs=[]
    for index in indices:
        weights=[local[o][d] for o,d in enumerate(digits_for(grid,index))]
        logs.append(math.fsum(math.log(v) for v in weights) if all(v>0 for v in weights) else -math.inf)
    normal=float(logsumexp(logs))
    if not math.isfinite(normal):raise ArithmeticError('重点组合没有有效概率质量；请检查系统偏差与输入范围')
    return np.exp(np.asarray(logs)-normal),error,normal


def conditional_event(grid,indices,weights,clauses):
    from .extreme_filter import ProblemIndex
    event=ProblemIndex(grid,clauses)
    hits=[event.matches(digits_for(grid,index)) for index in indices]
    return math.fsum(w for w,bad in zip(weights,hits) if not bad),math.fsum(w for w,bad in zip(weights,hits) if bad)


def focused_reconstruction(r,grid,indices,progress):
    from .reconstruction_joint import scan
    from .reconstruction_probability import attach
    bank=attach(scan(r,grid,None,progress,indices=indices),r)
    weights,error,logmass=bank['scope_weights'],bank['scope_mass_error'],bank['scope_log_mass']
    success,failure=conditional_event(grid,indices,weights,bank['red_factors'])
    value=dict(method='focus',value=success,failure=failure,grid_nodes=3,total=len(indices),
               local_checks=len(indices),pair_checks=len(indices),scale_nsigma=r.p.scale_nsigma,
               mass_error=error,scope_log_mass=logmass,scope='重点组合（含零位）内归一化联合概率；检查实际重构铜。',
               limitation='离散代表单元的条件模型良率，不是未选组合或连续全输入范围的良率。')
    return bank,value


def focused_width(analyzer,p,progress):
    """Shared focus-only width geometry for zero-view summaries and both yields."""
    from dataclasses import asdict,replace
    from .session_cache import logical_fingerprint
    from .resources import cancel_check
    p=replace(p,target_nsigma=0,reference_nsigma=p.scale_nsigma if math.isfinite(p.scale_nsigma) else p.reference_nsigma)
    model=analyzer.model;group=analyzer.cache.group
    key=('focused-width-v46',logical_fingerprint((model.data.geometry_id,model.data.top_name,model.roles,
         model.connections.order,asdict(p),analyzer.settings.width_key())))
    def compute():
        from .width_analysis import ADEQUATE,BLOCKED
        from .width_summary import strongest
        from .width_witness import channel_witness,edge_witness
        from .width_keepout import routing_violations
        from .width_event_ids import edge_keys
        from .width_yield import packed_events
        from .width_risk_probability import enrich
        from .extreme_filter import PoseClause
        grid,indices=focused_catalog(model,p);weights,error,logmass=conditional_weights(grid,p,indices)
        lines={};witnesses={};channels={};channel_witnesses={};red_factors=[];channel_factors=[]
        red=[];yellow=[];sites={};has_edge=False
        previous=analyzer.probability;analyzer.probability=None
        try:
            for number,index in enumerate(indices):
                cancel_check();digits=digits_for(grid,index);states=np.asarray([v[d] for v,d in zip(grid.locals,digits)])
                current=analyzer.frame({'states':states});rset=set();yset=set();channel_red=False
                clause=PoseClause(tuple((o,(d,)) for o,d in enumerate(digits)))
                for row in current['rows']:
                    if row['status']==ADEQUATE:continue
                    refs=[tuple(ref) for ref in row['refs']]
                    (rset if row['status']==BLOCKED else yset).update(refs)
                    channel_red|=row['status']==BLOCKED
                    witness=channel_witness(row,index)
                    strongest(channels,refs,row['status'],None,False,channel_witnesses,witness)
                    strongest(lines,refs,row['status'],None,False,witnesses,witness)
                    if row.get('probability_key') is not None:sites[row['probability_key']]=sites.get(row['probability_key'],0)|(1<<number)
                if channel_red:channel_factors.append(clause)
                edges=routing_violations(model,states,p,analyzer.settings);has_edge|=bool(edges)
                for row in edges:
                    refs=[(row['layer'],i) for i in row['indices']];rset.update(refs)
                    strongest(lines,refs,BLOCKED,None,False,witnesses,edge_witness(row,index,model,states))
                    for site in edge_keys(row):sites[site]=sites.get(site,0)|(1<<number)
                if rset:red_factors.append(clause)
                red.append(rset);yellow.append(yset)
                progress(round(100*(number+1)/len(indices)),f'重点组合宽度 {number+1}/{len(indices)}')
        finally:analyzer.probability=previous
        summary=dict(lines=lines,witnesses=witnesses,red_factors=red_factors,channel_lines=channels,
                     channel_witnesses=channel_witnesses,channel_factors=channel_factors,has_edge_risk=has_edge,
                     grid_key=tuple(v.tobytes() for v in grid.locals),package_states=len(indices),
                     selected_indices=indices,local_checks=len(indices),complete=True,scope='focus')
        bank=enrich(dict(method='focus',chunks=(packed_events(red,yellow,weights),),site_chunks=(sites,),
                         total=len(indices),scale_nsigma=p.scale_nsigma,mass_error=error,scope_log_mass=logmass,selected_indices=indices,grid_key=tuple(v.tobytes() for v in grid.locals)))
        return summary,bank
    return group.values.get_or_compute(key,compute) if group is not None else compute()
