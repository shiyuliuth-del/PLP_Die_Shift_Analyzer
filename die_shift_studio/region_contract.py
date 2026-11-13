"""Public V41 ABC names; internal C remains the historical moving support."""
EXPORT_ROLES=frozenset(('封装边框','芯片边框','固定RDL'))
PUBLIC_KIND={'A':'A','B':'C','C':'B','':''}


def verify_written_contours(path):
    """Read the actual DXF bytes before publication, independently of the writer.

    Every physical layer must be a closed, even-degree edge graph. Individual
    ABC entities are open. Positive-length coincident/duplicate edges fail.
    """
    from collections import Counter,defaultdict
    import math
    import re
    import shapely
    from .resources import cancel_check
    layers=defaultdict(list);record=None;section=None;pending=False
    def finish(row):
        if not row:return
        if row['closed'] or len(row['xy'])<2:raise ValueError('ABC输出含闭合实体或无效折线')
        points=row['xy']
        if points[0]==points[-1]:raise ValueError('ABC单条轮廓未在交界处断开')
        base=re.sub(r'[ABC]$','',row['name'])
        layers[base].extend(zip(points[:-1],points[1:]))
    with open(path,encoding='ascii') as stream:
        while True:
            a=stream.readline()
            if not a:break
            b=stream.readline()
            if not b:raise ValueError('DXF记录不完整')
            code,value=int(a),b.strip()
            if code==0:
                finish(record);record=None
                if value=='SECTION':pending=True
                elif value=='ENDSEC':section=None
                elif section=='ENTITIES':
                    if value!='LWPOLYLINE':raise ValueError('动静区仅允许开放轮廓实体')
                    record=dict(name='',xy=[],closed=False)
            elif code==2 and pending:section=value;pending=False
            elif record is not None:
                if code==8:record['name']=value
                elif code==70:record['closed']=bool(int(value)&1)
                elif code==10:record['xy'].append([float(value),None])
                elif code==20:record['xy'][-1][1]=float(value)
    summary={}
    for name,edges in layers.items():
        cancel_check();degree=Counter();unique=set();segments=[];length=0.
        for a,b in edges:
            a,b=tuple(a),tuple(b)
            if not all(math.isfinite(x) for x in (*a,*b)) or a==b:raise ValueError(f'{name} 含无效线段')
            edge=tuple(sorted((a,b)))
            if edge in unique:raise ValueError(f'{name} 存在重复轮廓线段')
            unique.add(edge);degree[a]+=1;degree[b]+=1
            line=shapely.LineString((a,b));segments.append(line);length+=line.length
        if any(n%2 for n in degree.values()):raise ValueError(f'{name} ABC交界点未严格闭合，未覆盖已有文件')
        merged=shapely.union_all(segments)
        if length-merged.length>max(1e-9,length*1e-12):raise ValueError(f'{name} 存在重叠轮廓线段')
        summary[name]=dict(segments=len(edges),nodes=len(degree),closed=True,overlap=False)
    if not summary:raise ValueError('未读回有效动静区轮廓')
    return summary
