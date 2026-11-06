"""Typeset definitions for the two joint width policies and their numerical method."""
from .width_yield import policy_texts,percentage_text


def append_report(q,step,grid,blocks):
    labels=('全修正良率','不修正良率')
    outputs=policy_texts(q,q.get('decimals',6))
    step('整包两种宽度策略良率',
         f"全部芯片输入范围及实际旋转中心，概率尺度 {q['scale_nsigma']:g}σ。",
         [r'F_{\rm repair}=I,\quad F_{\rm original}=R\cup A',
          r'Y_j=\int_H\mathbf{1}_{\{\mathbf{z}\notin F_j\}}f_H(\mathbf{z})\,d\mathbf{z}',
          r'Y_{\rm repair}-Y_{\rm original}=\Pr((R\cup A)\cap I^{\mathrm{c}})'],
         ' · '.join(name+' '+text for name,text in zip(labels,outputs)),
         'z 包含全部 DIE 的 X、Y、转角；同一DIE共享姿态，DIE之间按当前模型独立。H 是最大幅度输入盒，f_H 是盒内归一化联合正态密度。'
         'R、A 为所有红、黄问题的事件并集；I 仅合并忽略线路的红黄事件。全修正假设修复全部未忽略问题，不修正保留全部问题，只统计宽度。'
         '增加忽略集合时全修正良率不增加；事件相同或存在包含关系时，未全部忽略也可相等。修正收益独立累计，不能用浮点相减抹去尾部。')
    if q['method']=='grid':
        step('全情况分析：有限网格穷举',
             f"每活动轴 {q.get('grid_nodes')} 个节点（含端点），等价整包组合 {q['total']:,}；实际局部几何求解 {q.get('local_checks',0):,} 次。",
             [r'w_{i,r}=\int_{C_{i,r}} f_{H_i}(\mathbf{z}_i)\,d\mathbf{z}_i',
              r'\widehat{Y}_j=\sum_{r_1,\ldots,r_D}\left(\prod_{i=1}^{D}w_{i,r_i}\right)\mathbf{1}_{\{\mathbf{z}_{r_1,\ldots,r_D}\notin F_j\}}'],
             '按全部有限网格计算的概率估计；不使用随机采样。',
             'D为芯片数；i为DIE编号，r为该DIE局部节点编号。每轴相邻节点中点划分区间，首尾截在输入边界；C是各轴区间的直积。'
             'w是截断正态在区间内的积分，XY相关性保留；同DIE不能按独立孔相乘。零幅度轴锁零，不增加组合。'
             '共享几何事件使用决策图按概率合并，与显式枚举全部有限组合等价。连续空间仍有网格离散误差，须增加节点检查收敛，不能称为无限连续状态的有限精确穷举。')
    elif q['method']=='importance':
        step('全范围尾部增强积分',
             f"已计算 {q['samples']}/{q['total']} 个联合状态。常规区域、单DIE尾部和整包尾部共用同一概率模型。",
             [r'q=\frac{1}{2}q_0+\frac{1}{4D_a}\sum_{i\in A}u_iq_{0,-i}+\frac{1}{4}\prod_i u_i',
              r'w_m=\frac{f_H(\mathbf{z}_m)}{q(\mathbf{z}_m)},\qquad \widehat{Y}_j=\frac{\sum_m w_m\mathbf{1}_{\{\mathbf{z}_m\notin F_j\}}}{\sum_m w_m}'],
             '样本覆盖尾部，按密度比加权，不将尾部样本等权计为实际概率。',
             'A是幅度非零的DIE集合，Da是其数量。q0按截断X、给定X后的截断Y及独立截断角度依次生成；ui是该DIE活动维度输入盒上的均匀密度，q0,-i是其余DIE密度乘积。'
             '完全相关XY用一维支撑线处理，锁零轴不计维数。f_H使用原始联合正态在输入盒中的归一化常数；程序在对数域计算密度比。'
             '每个样本先合并全部失效再加权，重叠事件只计一次。忽略集合变更复用逐线路事件，不重新求几何。')
        if q.get('intervals'):
            step('加权积分的有限样本区间',
                 '固定样本数与固定忽略集合，联合置信度99%。',
                 [r'\bar{X}=\frac{1}{M}\sum_m X_m,\quad V=\frac{\sum_m X_m^2-M\bar{X}^{\,2}}{M-1}',
                  r'e=\sqrt{\frac{2V\ln(4K/\alpha)}{M}}+\frac{7B\ln(4K/\alpha)}{3(M-1)}',
                  r'L=\frac{\max(0,\bar{X}-e)}{U_w},\qquad U=\min\left(1,\frac{\bar{X}+e}{L_w}\right)'],
                 '成功和失效分别累计，区间见下表。',
                 'X分别取权重、两项成功权重、两项失效权重及修正收益权重，共K=6个有界量；α=0.01，M为样本数。'
                 'B是权重理论上界：一般为2，非退化相关XY按每DIE截断X质量qX除以联合XY质量Z的乘积扩大。'
                 '样本方差V使用M−1分母；经验Bernstein界给出各均值的上下界，Lw/Uw为总权重均值界。Lw为0时比值上界取1。'
                 '成功界与1减失效界取交集。按两侧与六个统计量作联合校正；连续选择忽略集合的所有历史查询不具有统一置信保证。')
            blocks.append('<p>区间依据：<a href="https://arxiv.org/abs/0907.3740">Maurer–Pontil，Empirical Bernstein Bounds，定理4</a>。</p>')
    else:
        step('全输入几何判定或已有数值记录',f"{q['samples']}/{q['total']} 个状态。",
             [r'Y_j=1-\Pr(F_j)'],'按上述两种策略计算。','确定静态或全输入界限已通过时可显示精确值；未分辨尾部不可视为零概率。')
    if q.get('intervals'):
        f=q.get('failure_intervals') or [(1-b,1-a) for a,b in q['intervals']]
        rows=[[name,percentage_text(a,fb,q.get('decimals',6)),percentage_text(b,fa,q.get('decimals',6))]
              for name,(a,b),(fa,fb) in zip(labels,q['intervals'],f)]
        blocks.append(grid(['宽度策略','99%置信下限','99%置信上限'],rows))
