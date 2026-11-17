"""Deterministic GD55 regression inputs; no simulated production distribution."""
from itertools import product


def cases():
    rows=[]
    def add(name,poses,purpose,centers=None):
        rows.append(dict(id=f'STRESS_{len(rows)+1:03d}',name=name,poses=poses,purpose=purpose,centers=centers or [None]*3))
    zero=lambda:[[0.,0.,0.] for _ in range(3)]
    add('ZeroBefore',zero(),'零位基线')
    for die in range(3):
        for axis,amount in [(0,15.),(1,15.),(2,1.)]:
            for sign in (-1,1):
                poses=zero();poses[die][axis]=sign*amount
                add(f'DIE{die+1}_{"XYR"[axis]}_{"NEG" if sign<0 else "POS"}',poses,'单DIE边界偏移，其余两颗锁零')
    for signs in product((-1,1),repeat=3):
        pose=[15.*signs[0],15.*signs[1],float(signs[2])]
        add('All_'+'_'.join('N' if v<0 else 'P' for v in signs),[pose[:] for _ in range(3)],'三DIE同向平移与旋转组合')
    for sign in (-1,1):
        add(f'MixedXYZ_{sign}',[[15.*sign,-15.*sign,1.],[-15.*sign,15.*sign,-1.],[0.,0.,.5*sign]],'多DIE相反平移与反向旋转')
        add(f'OpposingX_{sign}',[[15.*sign,0.,0.],[-15.*sign,0.,0.],[0.,0.,0.]],'相邻DIE相向/背向平移')
        add(f'RotationOnly_{sign}',[[0.,0.,sign],[0.,0.,-sign],[0.,0.,.5*sign]],'多DIE不同旋转角度')
    for die in range(3):
        poses=zero();poses[die]=[100.,-100.,3.]
        add(f'DIE{die+1}_OverRange',poses,'超出工程范围的强制断连压力输入，非工艺允许值')
    for die,(dx,dy) in enumerate([(99.996,-111.600),(86.970,100.220),(115.653,-124.732)]):
        poses=zero();poses[die]=[dx,dy,0.]
        add(f'DIE{die+1}_ForeignPadContact',poses,'已复现异网端孔碰接；超出工艺范围的断连压力输入')
    add('CustomPivot',[[2.,-1.,.1],[-1.,2.,-.1],[1.,1.,.05]],'绝对旋转中心(0,0)与混合偏移',[[0.,0.]]*3)
    add('SmallMixed',[[.01,-.005,.0001],[-.008,.004,-.00008],[.006,-.003,.00006]],'微小非零混合偏移回归')
    add('ZeroAfter',zero(),'问题封装之后的零位，验证不中断/不残留上一姿态')
    for i,row in enumerate(rows):row['position_um']=[(i%7)*12000.,(i//7)*12000.]
    return rows

def recovery_cases():
    import copy
    rows={r['name']:r for r in cases()}
    result=[]
    for i,name in enumerate(['ZeroBefore','DIE1_ForeignPadContact','SmallMixed','DIE2_ForeignPadContact','DIE3_ForeignPadContact','ZeroAfter']):
        row=copy.deepcopy(rows[name]);row['id']=f'RECOVERY_{i+1:03d}';row['position_um']=[(i%3)*12000.,(i//3)*12000.];result.append(row)
    return result

def ordinary_cases():
    """60 grid positions, independent per-DIE XYZ values, deterministic seed.

    Stratified sampling covers ordinary ranges without claiming a measured law.
    Values are explicit inputs, not formulas or production statistics.
    """
    import random
    rng=random.Random(490060);count=60
    columns=[]
    for die in range(3):
        axes=[]
        for extent in (8.,8.,.3):
            values=[round(-extent+(2*extent)*(i+rng.uniform(.15,.85))/count,6) for i in range(count)]
            rng.shuffle(values);axes.append(values)
        columns.append(axes)
    return [dict(id=f'ORDINARY_{i+1:03d}',name=f'Position_{i//10+1}_{i%10+1}',
                 poses=[[columns[die][axis][i] for axis in range(3)] for die in range(3)],
                 centers=[None]*3,position_um=[(i%10)*12000.,(i//10)*12000.],
                 purpose='普通混合偏移；每个位置/每颗DIE独立XYZ；固定种子490060，非生产分布') for i in range(count)]

if __name__=='__main__':
    import json
    print(json.dumps(dict(Ordinary=ordinary_cases(),Stress=cases(),FailureRecovery=recovery_cases()),ensure_ascii=False,indent=2))
