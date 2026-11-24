"""Public roles describe motion, not a fabrication-stage assumption."""
EXPORT_ONLY='一并导出'
ROLES=['不参与',EXPORT_ONLY,'封装边框','芯片边框','运动过孔','固定过孔','运动RDL','固定RDL','禁布区']
NON_PARTICIPATING=frozenset(('不参与',EXPORT_ONLY))
PHYSICAL={'运动过孔','固定过孔','运动RDL','固定RDL'}
MOVING={'运动过孔','运动RDL'}
ALIASES={'非金属图形':'不参与','芯片原始焊盘':'运动过孔','BGA接收盘':'固定过孔','晶圆段输出焊盘':'运动过孔',
         '晶圆段输出接口':'运动过孔','晶圆段过孔':'运动过孔','板段过孔':'固定过孔',
         '晶圆段RDL':'运动RDL','板段RDL':'固定RDL'}
def canonical_roles(roles,motions=None):
    out={};motions=motions or {}
    for k,r in roles.items():
        role=ALIASES.get(r,r)
        if r in ALIASES and r!='非金属图形' and r not in ('芯片原始焊盘','BGA接收盘') and k in motions:
            role=('运动' if motions[k]!='fixed' else '固定')+('RDL' if 'RDL' in r else '过孔')
        out[k]=role
    return out
