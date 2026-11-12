"""Auditable V19 calculation report: bounded inputs, probabilities, geometry."""
from html import escape
import math
from .core import PARAMETER_DESCRIPTIONS,fmt,Parameters
from .statistics import statistical_state,mean_and_bounds
from .distribution import normalizer
from .formula_render import MathDocument

def h(x):return escape(str(x))
def grid(headers,rows):
    return '<table width="100%" cellspacing="0" cellpadding="7" border="1"><tr>'+''.join('<th bgcolor="#edf2f8">'+h(x)+'</th>' for x in headers)+'</tr>'+''.join('<tr>'+''.join('<td>'+h(x).replace('；','；<br>')+'</td>' for x in row)+'</tr>' for row in rows)+'</table>'

def report_document(r,motions=None,progress=lambda *_:None):
    p=Parameters.from_dict(r['parameters']);count=r['die_count'];mu,cov,km=statistical_state(p.for_die(0),count);m=MathDocument();blocks=[]
    def step(title,inputs,formulas,outputs,detail=''):
        progress(min(95,len(blocks)*7),'生成报告公式：'+title)
        body=f'<h3>{len(blocks)+1:02d}　{h(title)}</h3><table width="100%" cellspacing="0" cellpadding="10" border="1">'
        for name,content,color in [('输入',h(inputs),'#edf3fa'),('公式 / 规则',m.block(*formulas) if formulas else '按真实几何与金属层序计算','#f8fafc'),('输出',h(outputs),'#edf6f1')]:
            body+=f'<tr><th width="80" bgcolor="{color}">{name}</th><td bgcolor="{color}">{content}</td></tr>'
        blocks.append(body+'</table><p>'+detail+'</p>')
    con=r['connections'];analysis=r.get('contact_analysis',{});_,bounds=mean_and_bounds(p.for_die(0),count);binary=math.isinf(p.target_nsigma)
    Z,Zerr=(1.,0.) if binary else normalizer(p.for_die(0),count)
    step('GDS、金属层序与几何修复',r['top_cell']+'；上→下：'+' → '.join(con['layer_order']),[],
         f"{count}颗芯片；{con['contact_count']}对相邻层连接；{analysis.get('network_count',0)}个几何网络；修复{len(r.get('repaired_geometry',[]))}个，忽略{len(r.get('ignored_geometry',[]))}个图形",
         f"工程节点已修改 {r.get('node_edit_count',0)} 个图形。几何版本 {h(r.get('geometry_revision','')[:16] or '源 GDS')}。零宽 PATH 警告 {len(r.get('import_warnings',[]))} 项。 "
         '长度统一为μm。边框、不参与和禁布层不建立电连接；同物理层号的datatype共用一个金属平面。Merge修复保留孔洞，失败图形用玫红细虚线框出并忽略，不改源GDS。')
    step('连接、芯片归属与跨芯片自动识别','同层接触与用户定义的相邻金属层重叠；逐DIE最深末端接触根',
         [r'E_{ij}=\mathbf{1}\{\operatorname{Area}(A_i\cap B_j)>\epsilon_A\},\quad\epsilon_A=\max(10^{-12},0.01\delta^2)',
          r'\mathcal{D}(A)=\bigcup_{B\in\mathrm{down}(A)}\mathcal{D}(B)'],
         f"运动归属待确认{len(con.get('ownership_notes',[]))}个；按位置归属{con.get('positional_ownership_count',0)}个；跨芯片几何网络{len(r.get('interdie_networks',[]))}个",
         'A、B为相邻层图形，δ为GDS网格（μm），εA为面积容差（μm²）。保留用户层序；空金属行不阻断连接。各DIE分别以最深的末端接触定义根，不能强迫不同堆叠共用一个全局底层。相邻参与金属层之间必须有真实面积重叠；即使某颗DIE局部没有中间层图形，也不跨过该参与层补造连接。此为几何推定，无法代替工艺网表。同层相连先合并，再沿下层追溯D(A)芯片集合；唯一根允许随动，多根/无根待确认，偏移时以橙色虚线标明并计入失败。全电连接分量包含两个以上DIE根即跨芯片网络。支持过孔—过孔、过孔—RDL等连接。只评估运动过孔与固定RDL的真实名义接口。芯片外扇出沿连接归属，绝不按最近芯片分配。完全没有连接到的运动层金属按位置归到最近的DIE（逐个图形算到芯片图形的距离），这类归属会记在附录里。')
    step('最大输入幅度与统一统计模型',f'Bx={bounds[0]:g} μm；By={bounds[1]:g} μm；Bθ={p.bound_angle_deg:g}°；目标={fmt(p.target_nsigma)}',
         [r'\sigma_x=\frac{B_x}{\kappa},\quad\sigma_y=\frac{B_y}{\kappa},\quad\sigma_\theta=\frac{B_\theta\pi}{180\kappa}',
          r'\Sigma_{11}=\sigma_x^2,\quad\Sigma_{22}=\sigma_y^2,\quad\Sigma_{33}=\sigma_\theta^2',
          r'\Sigma_{12}=\Sigma_{21}=\rho\sigma_x\sigma_y,\quad\Sigma_{13}=\Sigma_{23}=\Sigma_{31}=\Sigma_{32}=0',
          r'\mathcal{H}=\{z:|z_j-\mu_j|\leq B_j\},\quad Z=\int_{\mathcal{H}}f_N(z;\mu,\Sigma)\,dz',
          r'f_T(z)=\frac{f_N(z;\mu,\Sigma)\mathbf{1}_{\mathcal{H}}(z)}{Z}'] if math.isfinite(p.target_nsigma) else [r'I=\mathbf{1}\{\mathrm{current\ geometry\ contacts}\}'],
         f'截断前σx={math.sqrt(cov[0,0]):.8g} μm；σy={math.sqrt(cov[1,1]):.8g} μm；σθ={math.sqrt(cov[2,2]):.8g} rad；Z={Z:.12g}' if math.isfinite(p.target_nsigma) else '∞（无穷大符号）为当前几何二值模式，空白行检查名义位置，不表示无界正态尾部',
         'k可选择0～12或∞。公式中的尺度除数使用κ：非零有限k时κ=k，0时κ沿用最近有限级别（首次6）；0级α=0且不省略有界支持内尾部。B是围绕系统均值μ的最大随机偏移幅度；μ=0时DX/DY绝不超出±B。σ是截断前母正态尺度，截断后的实际标准差通常更小。Σ是母协方差，ρ为母X/Y相关系数，角度与平移独立；fN为母正态密度，H为允许状态盒，Z为盒内母概率，fT为归一化后密度。相关X/Y联合截断，不逐轴裁剪样本。零方差按确定值积分。∞模式直接绘制当前姿态的过孔实体。实体内部为100%，外部为0%。')
    step('输入单边幅度的极端预演','每颗DIE的最大X/Y/转角幅度与均值',
         [r'B_{ij}=0:\quad S_{ij}=\{0\}','B_{ij}>0:\\quad S_{ij}=\\{0,\\mu_{ij}-B_{ij},\\mu_{ij}+B_{ij}\\}',
          r'n_i=\prod_{j=1}^{3}|S_{ij}|,\qquad N=\prod_{i=1}^{m}n_i'],
         f"逐芯片姿态数 {r.get('die_state_counts',[])}；整包含首行共 {r.get('discrete_states',0):,} 种。"
         + (f"原图校验 {r['extreme_factors']['batches']} 批，另检查 {r['extreme_factors']['pair_states']} 个可能接近的芯片对姿态。重点非零姿态 {r.get('focused_states',0)} 个。离散足迹涉及 {r.get('footprint_vias',0)} 个运动孔，共 {r.get('footprint_poses',0)} 个实体位置。"
            if (r.get('extreme_factors') or {}).get('checked') else '原图离散校验尚未完成。'),
         'i 为芯片编号，j 分别取 DX、DY 和转角。μ 是系统偏差，B 是输入单边幅度。每轴集合 S 合并相同实际值。零幅度轴连同系统偏差锁在物理零位。nᵢ 是第 i 颗芯片的独立姿态数，N 是整包离散组合数。空白首行恢复名义全局判定。原图接口、包围、封装和障碍规则按归属合成，芯片间净距独立检查。离散足迹按各孔所属芯片全部姿态取实体并集；连续概率云按输入概率模型积分。多芯片耦合型变仅对重点姿态自动求解，全组合目录可逐行预演。')
    step('逐芯片旋转中心解析','共用或逐DIE旋转中心选项；GDS芯片边框；自定义相对坐标',
         [r'C=C_{\mathrm{die}}+c_{\mathrm{relative}}',r'T_z(P)=C+R(\theta)(P-C)+D'],
         '；'.join(f"DIE {row['die']}：{row.get('rotation_origin_mode','center')}，旋转中心{row.get('rotation_origin_um')} μm" for row in r['dies']),
         'Cdie为名义芯片最小外接矩形中心；正中心时crelative=(0,0)，角点取该矩形的实际顶点（按图纸Y分为上下两点，再按X分左右），自定义按图纸X/Y轴输入相对Cdie的μm坐标。C始终为源图参考点，不从偏移后图形重新求取。D=(DX,DY)为用户输入平移，R为逆时针旋转矩阵；芯片自身中心、边框、扇出线路和过孔均用同一C。')
    step('刚体偏移与控制区域' ,f'运动RDL包围={p.moving_annulus_um:g} μm；固定RDL包围a={p.annulus_um:g} μm；固定线距s={p.line_space_um:g} μm',
         [r'x^{\prime}=C_x+D_x+(x-C_x)\cos\theta-(y-C_y)\sin\theta',
          r'y^{\prime}=C_y+D_y+(x-C_x)\sin\theta+(y-C_y)\cos\theta',
          r'V_a=T_z(V)\oplus\mathcal{B}_a,\quad V_s=T_z(V)\oplus\mathcal{B}_{a+s}',
          r'I_i(z)=\mathbf{1}\{\min_{j\in\mathcal{F}_i}\operatorname{dist}(T_z(V_i),F_j)\leq a+s\}'],
         '包围区为青绿虚线，线距控制区为紫色虚线；红圈标接触点，半透明红色填充真实侵入交集',
         'C为所属DIE的实际旋转中心，x/y为原顶点，D为平移，θ以rad计算；T为刚体变换，V为真实过孔实体，⊕是Minkowski和，Br为半径r圆盘。Fi集合为相接固定RDL平面上全部其他几何网络图形，排除本网络。dist为最短距离；相切也违规，即使侵入面积为0。灰虚线接到当前运动后的孔中心。动画每帧重算控制区、接触点和交集。')
    if binary:
        step('当前姿态的二值接触','运动过孔实体、固定层异网铜形、最小包围量和线距。',
             [r'I_i(z)=\mathbf{1}\{\min_j d(T_zV_i,F_j)\leq a+s\}'],
             f"名义姿态共 {len(r['issues'])} 条接触记录（含各方案）。",
             'Vᵢ 为过孔，Fⱼ 为异网固定铜形，d 为实体最短距离，a 为包围量，s 为线距。')
    else:
        step('默认行概率判定与预演二值判定','有限k；允许上限α；当前接口及其全部其他网络',
             [r'\alpha=2\Phi(-k)\ (k>0),\quad \alpha=0\ (k=0),\quad p_i=\int_{\mathcal{H}}I_i(z)f_T(z)\,dz',
              r'p_i>\alpha\ \Longrightarrow\ \mathrm{insufficient}',
              r'I_i(z_{\mathrm{preview}})=1\ \Longrightarrow\ \mathrm{insufficient}'],
             f"允许接触概率={r['target_failure']:.8g}；不足记录{len(r['issues'])}条（含各方案）；待细化接口{len(analysis.get('unresolved',[]))}个" if math.isfinite(p.target_nsigma) else f"名义位置二值接触{len(r['issues'])}条（含各方案）",
             'Φ为标准正态CDF，α为每个接口允许的联合接触概率，不是各网络对概率之和，也不是已验证整包失效率。6σ的α约1.973×10⁻⁹。有限k空白行仅列已证明pi&gt;α的不足；具体极端/手动预演和∞一律按当前几何0/100%判断。良率级别变化时自动后台更新统计结果并保留预演；其他工艺输入保留上次评估，预演使用当前参数。')
        step('接触概率上下界：避免稀有事件漏判','独立标准正态u，线性变换z=μ+Lu，LLᵀ=Σ；自适应状态盒',
             [r'm_b=\prod_d[\Phi(h_{bd})-\Phi(l_{bd})]',
              r'\Delta_b=\sqrt{e_x^2+e_y^2}+2r_V\sin\!\left(\frac{\min(\pi,e_\theta)}{2}\right)',
              r'd_b>a+s+\Delta_b\Rightarrow\mathrm{safe},\quad d_b+\Delta_b\leq a+s\Rightarrow\mathrm{contact}',
              r'p_L=\frac{\sum_{b\in\mathcal{B}_{\mathrm{hit}}}m_b}{Z+\epsilon_Z},\quad p_U=\min\!\left(1,\frac{\sum_{b\in\mathcal{B}_{\mathrm{hit}}\cup\mathcal{B}_{?}}m_b+\epsilon_t}{Z-\epsilon_Z}\right)'],
             'pL>α才列不足；pU≤α才通过概率检查；其余显示“概率待细化”，可提高积分预算复算',
             'b为标准状态盒，l/h为各独立标准正态维的上下端点；mb为盒概率质量。盒经L变换后与硬输入边界相交，ex/ey/eθ为物理各维半宽；rV为该孔顶点到所属DIE中心最大距离；db为盒中间状态的过孔到其他网络最短距离。Δb是该盒所有状态的Hausdorff位移上界；仅整盒在H内且必接触才累计下界，部分越界/不能分类的盒保留上界。εt为未积分标准正态尾部的并集上界，εZ为归一化数值容差。按最大未定质量分裂状态盒，达到设置预算即停止。概率区间用于证明阈值关系，未声称给出收敛点估计；零采样命中不等于通过。')
    locations=analysis.get('locations',[])
    if analysis.get('location_threshold') is not None:
        step('位置事件与低概率标记',f'当前良率级别 {p.target_nsigma:g}，可进入运动范围的过孔与异网图形共 {len(locations)} 对。',
             [r'E_{ij}=\{d(T_Z V_i,C_j)\leq a+s\},\quad p_{ij}=\Pr(E_{ij}\mid H)',
              r'\alpha_k=2\Phi(-k),\qquad p_{ij}\leq U_{ij}\leq\alpha_k'],
             f"已证明低概率 {sum(q['low'] for q in locations)} 对，名义图仅标出有实际接触坐标的低概率事件。",
             'Vᵢ 为运动过孔实体，Cⱼ 为单个异网固定铜图形，T 是所属芯片姿态 Z 的刚体变换，d 是实体最短距离。a 是包围量，s 是固定线距，H 是硬输入边界。U 是积分上界，Φ 是标准正态分布函数。具体姿态仍显示二值接触，淡色仅表示事件在输入分布中的频度。')
    low_rows=[q for q in locations if q['low'] and q.get('witness')]
    if low_rows:
        blocks.append(grid(['运动过孔','固定铜图形','概率下界','上界','门限','可达接触坐标 / μm'],
                           [[f"{q['source_layer']} #{q['source_index']+1}",f"{q['target_layer']} #{q['target_index']+1}",
                             f"{q['lower']:.4g}",f"{q['upper']:.4g}",f"{q['threshold']:.4g}",
                             ', '.join(fmt(v) for v in q['witness']['point'])] for q in low_rows[:100]]))
    probabilities=analysis.get('probabilities',[])
    if probabilities:
        blocks.append('<h3>接口概率结果</h3>'+grid(['层/图形','DIE','下界','上界','允许上限','判定','积分盒数'],[[f"{q['source_layer']} #{q['source_index']+1}",q['die'],f"{q['lower']:.7g}",f"{q['upper']:.7g}",f"{q['threshold']:.7g}",q['verdict'],q['boxes']] for q in probabilities]))
    from .settings import ViewSettings
    cfg=ViewSettings.from_dict(r.get('view_settings'))
    from .drc_rules import effective_rules
    rule_width,rule_space=effective_rules(p,cfg)
    step('DRC正负容差',f'固定线宽w={p.line_width_um:g} μm；线距s={p.line_space_um:g} μm；统一容差τ={cfg.drc_tolerance_um:g} μm',
         [r'w_{DRC}=w-\tau,\qquad s_{DRC}=s-\tau,\qquad w_{DRC}>0,\ s_{DRC}>0'],
         f'实际DRC及型变验收门槛：线宽{rule_width:g} μm，线距{rule_space:g} μm',
         'τ是用户设置的带符号容差；正值放宽、负值收紧，默认0。线宽和线距共用该值，检查阈值按源DBU向上取整。包围量、接触概率、Space Map和三方案初筛仍使用工艺原值；异网短接始终报错，不由容差豁免。')
    if r.get('preview_drc_rules'):
        q=r['preview_drc_rules'];blocks.append('<h3>当前预演的DRC输入快照（可与上次评估不同）</h3>'+grid(['工艺线宽 / μm','工艺线距 / μm','容差 / μm','有效线宽 / μm','有效线距 / μm','包围量 / μm'],[[q[k] for k in ('nominal_width_um','nominal_space_um','tolerance_um','effective_width_um','effective_space_um','annulus_um')]]))
    if binary:
        step('当前姿态过孔实体覆盖','V 为运动过孔的源图形，T 为所选姿态的刚体变换，q 为图中位置。',
             [r'C(q)=\mathbf{1}\{q\in T_z(V)\}'], '实体内为100%，其余位置为0%。Space Map 使用工艺线距。')
    else:
        step('极端足迹：实体覆盖概率',f'方法={cfg.probability_method}；样本数=2^{cfg.sample_exponent}；积分阶数={cfg.quadrature_order}；网格={cfg.probability_cell_um:g} μm（0自动）',
             [r'p_V(q)=\int_{\mathcal{H}}\mathbf{1}\{q\in T_z(V)\}f_T(z)\,dz',
              r'\widehat p_{V,G}=\frac{1}{N}\sum_{n=1}^{N}\frac{|G\cap T_{z_n}(V)|}{|G|},\quad\int p_V(q)\,dq=|V|',
              r'\widehat p_{V,G}\simeq\sum_j\omega_j(H*K_j)_G',
              r'\omega_j=\frac{w_j}{2},\quad\theta_j=\mu_\theta+\sigma_\theta\Phi^{-1}\!\left(\Phi(-k)+\frac{h_j+1}{2}[\Phi(k)-\Phi(-k)]\right)'],
             f'半透明热图表示整孔覆盖概率；显示色阶采用p的γ次方，γ={cfg.probability_gamma:g}；低概率增强不改变概率网格或边界',
             'q为空间位置，G为网格，|·|为面积，N为样本数。Monte Carlo/Sobol采用同一相关母分布并联合拒绝超界状态；Sobol拒绝后仅为准随机近似，不沿用原平衡保证。确定性方法用[-1,1] Gauss–Legendre节点hj/权重wj，通过截断正态逆CDF映射得到θj；ω为角度积分权重（和为1），H为相关平移落入每格的条件概率，Kj为该朝向完整孔实体面积掩模，*为卷积。全部平移格覆盖硬输入范围并按Zxy归一化。面积积分应接近孔面积，格值之和不归一为1；角度/栅格/FFT有有限数值精度，可加密复算。低概率尾部可能没有采样命中或低于FFT有效精度，不伪造颜色补齐。极端包络外不可达，包络内也并非处处有可见概率。多个孔透明叠色不是联合覆盖率。')
        step('Space Map：所需线距',f'固定线距s={p.line_space_um:g} μm；连接归属；母协方差；芯片和扇出域',
             [r'A=P-C,\quad b=R(\mu_\theta)A,\quad J_{11}=J_{22}=1,\quad J_{12}=J_{21}=0',
              r'J_{13}=-b_y,\quad J_{23}=b_x',
              r'k_M=\sqrt{F^{-1}_{\chi^2_3}\!\left(1-\frac{\alpha Z}{M}\right)}',
              r'Q_N=\|\mu_{xy}+b-A\|+k_M\sqrt{\lambda_{\max}(J\Sigma J^T)}+\frac{\|A\|(k_M\sigma_\theta)^2}{2}',
              r'Q_H=\|\mu_{xy}+b-A\|+\sqrt{B_x^2+B_y^2}+2\|A\|\sin\!\left(\frac{\min(\pi,B_\theta)}{2}\right)',
              r'S(P)=s+\min(Q_N,Q_H)'],
             '整DIE及其外部随动金属显示需求；整包模式其他区域为基础线距；0级使用硬边界QH；∞模式仅显示基础线距',
             'P为原始位置（μm），C为所属DIE的实际旋转中心，R为二维旋转矩阵，A/b为原/均值旋转相对坐标，J为位移Jacobian，λmax为2×2协方差矩阵最大特征值；其计算为(v11+v22+√((v11−v22)²+4v12²))/2。M为DIE数，F为3自由度卡方CDF（代码用逆生存函数避免消减）；Z为截断归一化质量。QN为条件置信包络，QH为硬输入范围的确定性上界，Bθ此处用rad。取两者较小仍是保守上界。S不包含孔半径或包围量，不直接用来替代接触概率判定。∞没有统计补偿量。')
    step('共用动区与重构预演','原始固定RDL、名义连接关系、全部输入偏移边界、两端手动位置',
         [r'G=A\cup B\cup C,\quad A^\circ\cap B^\circ=A^\circ\cap C^\circ=B^\circ\cap C^\circ=\varnothing',
          r't=\mathrm{clip}\!\left(\frac{s(P)-s_0}{s_1-s_0},0,1\right),\quad w(t)=1-10t^3+15t^4-6t^5',
          r'P_A^\prime=P+w(t)[T(P)-P],\quad P_B^\prime=P,\quad P_C^\prime=T(P)'],
         'A动区、B固定连接静区、C运动连接平移区；原图路径与端点对所有姿态共用。',
         'G是原始固定铜；s(P)为P到名义铜内追溯路径的投影弧长，s0/s1是有序端点。T为所属DIE的刚体变换，w为零端部导数的过渡权重。孔盘全纳入A时，运动端权重平台覆盖完整孔盘。端点不得交换，拐角内部吸附到预留外。自动比较拓扑可切分的直段，优先最长且满足全输入位移包络余量的候选；不是全局路由最优证明。预演检查有效性、连通和区域交叠，失败保留原图并标紫色；异网净距按最终铜测量，完整DRC需单独启用。')
    step('动静区DXF轮廓','当前完成铜形、同一A/B/C区域定义、输出范围/Merge/孔洞拆层/比例',
         [r'E_K=\partial G^\prime\cap K^\prime,\quad K\in\{A,B,C\},\qquad \bigcup_K E_K=\partial G^\prime'],
         '固定RDL层名追加A/B/C；仅保留真实边界的开放折线，不输出内部切割边或HATCH。',
         '∂为外边界及孔洞边界。各区的折线在同一接口端点对接，叠加后才还原完整闭合轮廓。其他图层保持原层名；源GDS和工程几何均不由输出选项修改。')
    step('工程缓存与无位移恢复',f'当前工程普通缓存上限={cfg.cache_max_entries}；GDS内容、层序/用途、偏移、工艺与计算设置',
         [],'按实际内容复用状态、概率、Space Map及已验证动画；无位移帧固定保留。',
         '缓存上限按一个工程的普通结果条目数计，达到上限时淘汰最久未使用条目；固定无位移结果不占普通额度。取消的动画保留已完成帧，重试接着计算；反向切换复用已验证路径帧。目标状态缓存独立于过渡动画，缓存命中优先显示。后台按当前CPU和内存余量至多并行两项预演，计算引擎独占，结果缓存共享；高负载排队，过时结果只入缓存不覆盖新画面。工程切换保留本次运行中各工程缓存；清理菜单可清当前或全部工程，文件大小每0.5秒刷新。主动重新方案评估清当前工程，关闭软件清全部会话缓存，不从用户工程反序列化缓存文件。')
    step('三方案空间判据：一个需求／空间比值', '硬输入最大幅度、实际旋转中心、几何网络、实际RDL平面、线宽线距及固定端',
         [r'Q_i=\sqrt{(|\mu_x|+B_x)^2+(|\mu_y|+B_y)^2}+2R_i\sin\!\left(\frac{\min(\pi,|\mu_\theta|+B_\theta)}{2}\right)',
          r'n_b=\left\lceil\frac{N_b}{L}\right\rceil,\quad W_b=n_bw+(n_b-1)s+2Q\quad(N_b>0)',
          r'M_i=\min\!\left\{d(V_i,F_{\ne i})-a-s,\ d(V_i,\partial P)-a,\ d(V_i,K)-a-s,\ \min_{j\ne i}\frac{d(V_i,V_j)-2a-s}{2}\right\}',
          r'U_1=\max_b\frac{W_{\mathrm{chip},b}}{\ell_{\mathrm{chip},b}},\quad U_2=\max_i\frac{Q_i}{M_i}',
          r'U_3=\max\!\left(\max_b\frac{W_{\mathrm{fanout},b}}{\ell_{\mathrm{fanout},b}},\ \max_b\frac{W_{\mathrm{moving},b}}{\ell_{\mathrm{chip},b}}\right)',
          r'U\leq1\Rightarrow\mathrm{screening\ candidate},\quad s^*=\operatorname{arg\,min}_{s\in\mathcal{S}_{\mathrm{eligible}}}U_s'],
         r['recommendation']['name']+'；'+r['recommendation']['reason'],
         'Q为硬输入盒下保守位移（μm），Ri是相关过孔实体顶点到其DIE实际旋转中心最大距离；各运动接口取最大Q作为束边界补偿。μ为均值，Bx/By为单边μm，Bθ为rad。此初筛始终比较完整输入幅度，独立于概率阈值。Nb为最近切边分配的不同几何网络数；L为GDS实际物理RDL层数（不重复计算datatype，并不超过参数层数）；n为单层最多轨道数，w/s为对应工艺规则。每束只加2Q，同DIE线路不被当成独立随机移动；Nb=0时W=0。方案1切边是DIE矩形，方案3切边为与DIE同方向且覆盖DIE及全部实际接口的矩形；运动层原始逃逸带宽使用Q=0及运动规则。方案2的Vi是沿网络连接追溯到该DIE的固定过孔；F≠i为同接收平面的其他网络固定RDL，P为封装，K为禁布区，j只取相同平面其他网络固定过孔。距离为实体最短距离，无对应障碍的项为∞；共享净间距均分给两端，Mi≤0则U2=∞。固定端空间可用于扩大孔盘，不只取现有孔盘半径。原始铜是否能按该模型路由、全层随动是否碰撞以及预制制造阶段仍须详细验证。机械净距/跨DIE刚体冲突/归属作为独立门槛。各方案U最小优先，仅同分按2→1→3；原接口概率不合并成U、不声明制造放行。')
    blocks.append(grid(['方案','状态','U','原接口概率','理由'],[[s['scheme'],s['status'],fmt(s['gamma']),s.get('contact_status',''),'; '.join(s['reasons'])] for s in r['schemes']]))
    metrics=r.get('screening',{})
    blocks.append(grid(['DIE','U1','U2','U3','固定层数','运动层数'],[[q['die'],fmt(q['u1']),fmt(q['u2']),fmt(q['u3']),metrics.get('fixed_layers'),metrics.get('moving_layers')] for q in metrics.get('rows',[])]))
    for q in metrics.get('rows',[]):
        blocks.append('<h3>DIE '+str(q['die'])+'：各项计算展开</h3>'+grid(['切边模型','边','网络N','每层n','层L','w / μm','s / μm','Q / μm','需求W / μm','空间ℓ / μm','W/ℓ'],
            [[name,side,tr['counts'][i],tr['tracks_per_layer'][i],tr['layers'],tr['width_um'],tr['space_um'],fmt(tr['travel_um']),fmt(tr['demand_um'][i]),fmt(tr['lengths_um'][i]),fmt(tr['demand_um'][i]/tr['lengths_um'][i])] for name,tr in [('芯片边',q['route']),('扇出边',q['fanout']),('运动层',q['pre_escape'])] for i,side in enumerate(('左','右','下','上'))]))
        blocks.append(grid(['固定过孔层/编号','位移Q / μm','异网裕量','封装裕量','禁布裕量','孔对半裕量','最小M / μm','Q/M'],[[v['layer']+' #'+str(v['index']+1),fmt(v['need_um']),*[fmt(v.get('margin_terms',{}).get(k)) for k in ('foreign_margin_um','package_margin_um','keepout_margin_um','pair_half_margin_um')],fmt(v['free_um']),fmt(v['ratio'])] for v in q['captures']]))
    safe=r.get('safe_ranges',{})
    step('连续无异常接触范围：较大体积安全内盒',f'硬输入幅度；原始固定RDL；搜索端点精度={cfg.safe_box_precision:g}倍单边幅度；连续验证预算={cfg.safe_box_budget}盒；起点数={cfg.safe_box_starts}',
         [r'\mathcal{B}_d=[l_x,h_x]\times[l_y,h_y]\times[l_\theta,h_\theta]\subseteq[\mu-B,\mu+B],\quad\mu\in\mathcal{B}_d',
          r'v(\mathcal{B}_d)=\prod_{j:B_j>0}\frac{h_j-l_j}{2B_j}',
          r'd(T_m(V_i),F_{\ne i})>a+s+\sqrt{e_x^2+e_y^2}+2R_i\sin\!\left(\frac{\min(\pi,e_\theta)}{2}\right)\Rightarrow\mathrm{safe}',
          r'\varepsilon_{arc}=2R_i\sin^2\!\left(\frac{\Delta\theta}{4n}\right),\quad E=\operatorname{conv}\!\{R(\theta_t)(V_i-C)+C+D_{xy}^{corner}\}\oplus\mathcal{B}_{\varepsilon_{arc}+\epsilon}',
          r'd(E,F_{\ne i})>a+s\Rightarrow\mathrm{safe\ for\ every\ pose}'],
         '逐DIE输出X/Y/O最小最大值；只有整盒证明安全才显示数值。均值接触或归属不完整时不输出伪安全范围。',
         'l/h为候选各轴端点；v为占原输入盒的体积分数，零幅度轴从乘积移除（全零为1）。采用对称缩放起点和多个端点扩张顺序，分别调整六个正负端点；保留体积较大的已证安全盒，未证明全局最大。m/e为候选状态盒中点/半宽；先用Hausdorff界证明，再构造整孔旋转弧及平移矩形的外包络。θt为n等分角度节点，Δθ为该盒角度全宽，conv为凸包，εarc为弦高上界，ε为DBU数值保护，圆盘采用外接离散补偿；凹角/孔洞可被填平但不会低估占据区域。若包络还不能证明，继续分裂贡献最大的状态维；实际接触见证或耗尽预算均拒绝候选。安全结论不依靠极端点或随机采样；所有DIE可组合，因为被检查固定RDL保持原始状态。范围包含输入均值，默认均值0；无概率阈值豁免，∞同样搜索硬幅度。范围仅证明真实运动孔控制区对相邻固定RDL异网无接触，不包括预演型变、全层DRC或芯片机械碰撞。')
    blocks.append(grid(['DIE','X最小 / μm','X最大 / μm','Y最小 / μm','Y最大 / μm','O最小 / °','O最大 / °','体积分数','状态'],
        [[q['die'],*([fmt(q['minimum_display'][0],6),fmt(q['maximum_display'][0],6),fmt(q['minimum_display'][1],6),fmt(q['maximum_display'][1],6),fmt(q['minimum_display'][2],8),fmt(q['maximum_display'][2],8)] if q.get('certified') else ['—']*6),fmt(q.get('volume_fraction'),6) if q.get('certified') else '—',q['status']] for q in safe.get('rows',[])]))
    yr=r.get('model_yield')
    if yr:
        from .safe_range_plot import format_yield
        if binary:
            step('名义姿态的模型结果','所有已识别接口在原始固定铜上的二值接触',
                 [r'Y_0=\prod_i (1-I_i(0))'],format_yield(yr['estimate']),'缺少可验证接口或归属未定时显示不可用。')
        else:
            step('全输入范围的无接触模型良率',f'各DIE全部硬输入范围；原始固定RDL；{yr["method"]}',
                 [r'Y_d=\int_{\mathcal{H}_d}f_{T,d}(z)\prod_{i\in\mathcal{I}_d}\mathbf{1}\{d(T_z(V_i),F_{\ne i})>a+s\}\,dz',
                  r'Y=\prod_{d=1}^{D}Y_d,\qquad\mathcal{Y}_{target}=[1-2\Phi(-k),1]'],
                 '整包模型估计='+format_yield(yr['estimate'])+'；'+yr['interval_kind']+'=['+format_yield(yr['lower'])+', '+format_yield(yr['upper'])+']',
                 'D为DIE数，d为DIE编号（距离函数d(·,·)则表示实体最短距离），Id为该DIE所有真实运动过孔—固定RDL接口；H及fT为前述完整硬输入盒及其归一化截断密度。Vi为孔实体，Tz为采用真实旋转中心的刚体变换，F≠i为对应接收层全部其他几何网络，a为最小包围量，s为固定层线距。每个样本让同一DIE所有孔共同移动；不同DIE假设独立，因此只对DIE概率相乘，不对过孔概率相乘。积分包括安全内盒之外的状态。目标色带是用户k对应的数值参考，不代表每接口满足该阈值就保证整包达标；k=0时目标为1；∞按名义几何二值输出。图标题与倒三角显示估计值，区间表示精度，缺失接口/归属不明时不输出虚构百分比。范围只含原始固定RDL异网接触，不含型变后动态布线或其他生产失效。')
            step('良率积分精度与计算预算',f'每DIE样本数2^{cfg.yield_sample_exponent}；状态盒预算{cfg.yield_box_budget}；确定性区间宽度目标{cfg.yield_tolerance:g}',
                 [r'\widehat{Y}_d=\frac{x_d}{N_d},\quad\widehat{Y}=\prod_d\widehat{Y}_d,\quad\eta=\frac{0.01}{D}',
                  r'L_d=B^{-1}_{x_d,N_d-x_d+1}\!\left(\frac{\eta}{2}\right),\quad U_d=B^{-1}_{x_d+1,N_d-x_d}\!\left(1-\frac{\eta}{2}\right)',
                  r'L=\prod_dL_d,\quad U=\prod_dU_d',
                  r'L_d^{box}=\sum_{B\in\mathcal{S}_d}P_{-}(B),\quad U_d^{box}=1-\sum_{B\in\mathcal{C}_d}P_{-}(B)',
                  r'P_{-}(B)=\max\!\left(0,\frac{P_N(B)-\varepsilon_B}{Z_d+\varepsilon_Z}\right),\quad\widehat{Y}^{box}=\frac{L^{box}+U^{box}}{2}'],
                 '抽样：输出99%联合置信区间；不随机采样：输出确定性几何积分区间，预算耗尽保留未定质量。',
                 'Nd是每DIE有效独立样本数，xd为全部接口同时无接触的样本数；抽样来自前述完整截断正态，种子固定可复算。B⁻¹(a,b)为Beta(a,b)分布逆CDF；xd=0令Ld=0，xd=Nd令Ud=1。η是每DIE的区间失覆盖预算；Bonferroni使所有DIE区间同时覆盖率至少99%，同一随机模型下乘积区间保护整包概率。零失效样本不能认证100%或6σ放行；只有整个支持域几何证明安全才给确定的100%。确定性方法将输入盒二分：Sd/Cd是所有接口均无接触/至少一个接口必接触的互斥子盒集合，PN(B)为母正态盒概率，由相关XY条件CDF积分乘独立角度CDF计算；εB与εZ为数值积分误差保护，P−(B)为减去误差后的子盒概率下界。对中点m、半宽e，使用前述Hausdorff位移δ：每孔距离> a+s+δ证明整盒安全；存在孔距离+δ≤a+s证明整盒接触。其余按空间变化最大的轴继续分裂，优先未定质量大的盒。确定性区间含未定质量与数值误差，不是随机置信区间。')
        blocks.append(grid(['DIE','方法状态','模型估计','下界','上界','有效样本/状态盒'],[[q['die'],q['status'],format_yield(q['estimate']),format_yield(q['lower']),format_yield(q['upper']),q.get('samples',q.get('boxes',0))] for q in yr['rows']]))
    if p.per_die_enabled and not binary:
        step('逐芯片输入解析','GDS识别的DIE编号与独立单边幅度',
             [r'\sigma_{x,d}=\frac{B_{x,d}}{\kappa},\quad\sigma_{y,d}=\frac{B_{y,d}}{\kappa},\quad\sigma_{\theta,d}=\frac{\pi B_{O,d}}{180\kappa}'],
             '每颗芯片均使用自己的幅度；前述单组数值示例为DIE 1，几何连通归属决定实际调用哪组。',
             'd为DIE编号；Bx,d和By,d是μm单边最大幅度，BO,d是度，k为目标级别。有限k采用截断联合正态，∞为名义位置二值判断。')
        blocks.append(grid(['DIE','Bx / μm','By / μm','BO / °','σx / μm','σy / μm','σθ / rad'],
            [[i+1,*v,v[0]/p.scale_nsigma,v[1]/p.scale_nsigma,math.radians(v[2])/p.scale_nsigma] for i,v in enumerate(p.die_bounds)]))
    recon=r.get('preview_reconstruction')
    if recon:
        detail=recon['summary'];parameters=recon.get('parameters',{})
        blocks.append('<p>共用动区 '+str(detail['pieces'])+' 处；警告 '+str(detail.get('warnings',0))+' 处。'+h(recon.get('scope',''))+'</p>')
        space=detail.get('rule_space_um',0.)
        from .connection_curves import STYLES
        style=parameters.get('connection_style','c2')
        step('动区连接样式',f"连接样式：{STYLES.get(style,style)}；共用原图两端截面。",
             [r'P^{\prime}(s)=P(s)+w(t)\left[T(P(s))-P(s)\right],\quad t=\frac{s-s_0}{s_1-s_0}',
              r'w_1(t)=1-3t^2+2t^3,\qquad w_2(t)=1-10t^3+15t^4-6t^5'],
             '一阶/二阶默认使用三次/五次位移权重，交错时比较匹配端部切向的三次/五次贝塞尔回头候选；直线连接对应交点且贴紧限位，圆弧使用分段相切双圆弧。蓝黄两端均可保留整孔盘。合法但违规的候选标红；无法连接时移除连接段、保留两端，红色提示不作为铜或DXF输出。',
             'P为原图边界点，s为沿名义追溯路径的弧长，s₀、s₁为共同端点，t截断到[0,1]；T为该DIE刚体变换。w₁端部一阶导数为0，w₂端部一阶/二阶导数为0。整孔盘模式保留原孔盘实体，在孔盘外接线，不能把蓝点当成零宽导体。')
        def state(q):
            if q['orphan']:text='整块随动（无不动铜可接）'
            elif q['gap_um'] is not None and q['gap_um']<=1e-6 and q.get('bridge_area_um2') is not None:text='贴合（由补线覆盖）'
            elif q['gap_um'] is not None and q['gap_um']<=1e-6:text='贴合（直接相接）'
            elif q.get('bridge_area_um2') is None:text='无需补线（无切口面）'
            else:text='已补线'
            clearance=q.get('clearance_um')
            if clearance is not None and space:
                if clearance<space-1e-6:text+='｜间距不足'
                elif clearance<2.*space-1e-6:text+='｜间距临界'
            if q.get('manual'):text+='｜手动切口'
            return text
        blocks.append(grid(['DIE','固定RDL层','图形','落盘数','网络','移动区面积 / μm²','位移 / μm','裂隙 / μm','补线面积 / μm²','切口缝长 / μm','间距 / μm','状态'],
            [[f"DIE{q['die']+1}",q['layer'],q['index']+1,len(q.get('vias',(1,))),q['net'],fmt(q['movable_area_um2']),fmt(q['shift_um']),
              '—' if q['gap_um'] is None else fmt(q['gap_um']),
              '—' if q.get('bridge_area_um2') is None else fmt(q['bridge_area_um2']),
              '—' if q['bridge_length_um'] is None else fmt(q['bridge_length_um']),
              '—' if q.get('clearance_um') is None else fmt(q['clearance_um']),
              state(q)] for q in recon['pieces'][:100]]))
        if len(recon['pieces'])>100:blocks.append(f"<p>重构移动区共{len(recon['pieces'])}块，报告列出前100块；“重构预演”表可定位当前全部块。</p>")
    reconstruction_yield=r.get('reconstruction_yield')
    if reconstruction_yield:
        q=reconstruction_yield
        from .width_yield import percentage_text
        step('重构良率',f"完整输入分布；每轴{q['grid_nodes']}节点，整包{q['total']:,}个有限网格姿态。",
             [r'Y_{\rm recon}=\sum_{\boldsymbol j}\left[\prod_d m_{d,j_d}\right]\mathbf{1}\!\left(\bigcap_r\{g_r(G(\boldsymbol z_{\boldsymbol j}))\geq0\}\right)',
              r'm_{d,j}=\int_{C_{d,j}} f_d(x,y,\theta)\,dx\,dy\,d\theta'],
             '重构良率 '+('上界≤' if q.get('partial') else '≈')+percentage_text(q['value'],q['failure'],6),
             'd为DIE编号；j_d是该芯片的网格节点，C_d,j为相邻中点划分的概率单元；f_d是输入幅度内归一化的联合截断正态，保留XY相关性。G为该整包姿态下实际重构图形；r包括固定RDL线宽、线距、连接过孔包围量、封装边距和禁布区；g_r≥0表示该规则通过。所有线路共用所属DIE状态；按电网络和相互作用对分解事件并集，不能相乘各孔边缘良率。'+q['limitation'])
    policy=r.get('width_policy_yield')
    if policy:
        from .width_yield_report import append_report
        append_report(policy,step,grid,blocks)
    width=r.get('preview_width_analysis')
    if width:
        summary=r.get('width_extreme_summary')
        if summary:
            step('首行全组合宽度风险',f"全部离散姿态共 {summary['states']:,} 种。每条线路取最重等级。",
                 [r'L_{\min}=Nw+(N-1)s+m_a+m_b,\qquad L_{\rm keep}=\sum_i\max(w_i,w)+(N-1)s+m_a+m_b'],
                 f"风险线路 {summary['lines']} 条，已证超限 {summary['exceeded']} 条，概率待定 {summary['undetermined']} 条，二值 {summary['binary']} 条。",
                 'mₐ、mᵦ 为两侧边距：禁布区侧为0，其他侧为s。N 是名义贯穿根数，w、s 是扣除容差后的最小线宽和线距，wᵢ 是各线原宽，单位均为 μm。有限级别仅在位置事件下界大于门限时画实线，不能证明的风险保留虚线。')
        parameters=width['parameters']
        step('当前姿态的固定 RDL 通道容量',
             f"名义图纸的穿越线路，有效线宽 {fmt(parameters['width_um'])} μm，有效线距 {fmt(parameters['space_um'])} μm，最小包围量 {fmt(parameters['enclosure_um'])} μm。",
             [r'W(n)=nw+(n-1)s+m_a+m_b',r'C(g)=\max\left(0,\left\lfloor\frac{g-m_a-m_b+s}{w+s}\right\rfloor\right)',
              r'C_{0,j}=C(g_{0,j}),\quad C_{t,j}=C(g_{t,j})',r'u_i=2\,\mathrm{dist}(m_i,\partial R_i),\quad W_{\rm keep}=\sum_{i=1}^{N}\max(u_i,w)+(N-1)s+m_a+m_b',r'\Delta W=\max(0,W_{\rm keep}-g_t)\quad(N>0)'],
             f"相邻通道截面 {width['examined']} 个，宽度足够 {width['counts']['宽度足够']} 个，需局部收窄 {width['counts']['需局部收窄']} 个，宽度不足 {width['counts']['宽度不足']} 个。",
             'w、s 为工艺固定线宽、线距分别减去共用 DRC 容差。n 根线所需宽度 W(n) 包含 n−1 道线间距及两壁边距 mₐ、mᵦ；普通孔盘、固定金属和封装边界取 s，禁布区侧取 0。禁布区固定且无电连接，可贴边不可进入。'
             'N 为原图确实穿越截面的线路根数。g₀ 为原净宽，gₜ,ⱼ 为运动后第 j 段可用净宽。C₀ 和 Cₜ 分别为原可通与现可通根数。'
             '孔和孔盘构成定宽的实体壁。另一孔盘进入评估矩形时，将整块投影区间作为占用，由相邻两壁分别计算。有独立可识别的同心孔盘时保留较大外形。无运动过孔的固定 RDL 网络整块占用。'
             'mᵢ 是第 i 条线路与截面相交区间的中点，∂Rᵢ 是其名义铜边界。uᵢ 是局部铜厚度的平面量测值，对直线斜穿同样给出横向原宽。'
             'W_keep 是原宽和工艺下限取大后所需的总宽。只计该对相邻壁间原有的自由线路，连接本地孔盘的部分已经由实体占用。原宽可放入时为绿色。原宽放不下而 Cₜ ≥ N 时为琥珀色。Cₜ < N 时为红色。'
             '矩形中心和角度由本对实体的实际最近边界点确定。只有同一片原始线路铜贯穿矩形的两端面才登记，空通道不输出。同一实体对及相同有序线路的连续等距截面归并，保留最紧的一处。ΔW 是线路合计需要收窄的宽度。红色表示即使全部达到工艺下限仍不能通过。整体布通仍需复核绕行侧别。封装外缘和内孔对线路均预留本层的一道有效线距。')
        blocks.append(grid(['DIE','DX / μm','DY / μm','O / °'],[[i+1,fmt(z[0]),fmt(z[1]),fmt(math.degrees(z[2]))] for i,z in enumerate(width['states'])]))
        if 0<p.target_nsigma<math.inf:
            step('通道风险的概率上界','该截面两壁、原始穿越线路、各芯片独立或共享的真实运动与当前良率级别。',
                 [r'L_{\min}=Nw+(N-1)s+m_a+m_b,\quad L_{\rm keep}=\sum_{i=1}^N\max(w,w_i)+(N-1)s+m_a+m_b',
                  r'd(A,B)\geq\min_{b\in B}n^Tb-\max_{a\in A}n^Ta,\quad \Pr(d<L\mid H)\leq U'],
                 '仅在 U 不超过门限时淡显。收窄用保宽需求，宽度不足用工艺最小需求。',
                 'mₐ、mᵦ 为两壁边距，禁布区侧为0，其他侧为s。A、B 为两侧真实实体，n 是名义截面从 A 指向 B 的单位向量。N 是必过线路根数，wᵢ 是第 i 根的原始线宽。沿 n 计算旋转区间内的最小投影间距及截断正态平移尾概率，再按角度质量相加。无法证明或发生分段的位置保留正常颜色。')
        blocks.append(grid(['固定 RDL','状态','通道','原可通','必须通过','现可通','预期收窄 / μm','位置 / μm','位置事件上界'],
                           [[q['plane'],q['status'],' ↔ '.join(q['pair']),q['original'],q['required'],q['current'],fmt(q.get('narrowing_um',0)),
                             str([round(x,3) for x in q['point']]),f"{q['event_probability']['upper']:.4g}" if q.get('event_probability') else '—'] for q in width['rows'][:100]]))
        if width.get('displayed',len(width['rows']))>100:blocks.append('<p>报告列出前 100 个选定类别的截面。完整清单见图形下方的宽度分析表。</p>')
        edge=width.get('edge_violations',[])
        step('线路到封装边框与禁布区',f'当前姿态下的运动和固定 RDL 以及封装边框、禁布区，共 {len(edge)} 个不足图形。',
             [r'A_{\rm allowed}=B\ominus D_{s_k},\qquad E_i=P_i\cap A_{\rm allowed}^{\rm c}'],
             f'封装边缘或禁布区违规 {len(edge)} 个图形',
             'B 是封装有效区域，含内孔。D 是半径等于本层有效线距 sₖ 的圆盘，Pᵢ 是当前实际铜轮廓。Eᵢ 为落在允许区域以外的铜。恰好达标允许。另对禁布区域 K 检查当前铜内部与 K 内部的交集；仅共边、共点允许，正面积侵入标红。不对 K 加线距或 DRC 容差缓冲。')
        if edge:
            blocks.append(grid(['类型','层','图形编号','实测净距 / μm','要求 / μm','侵入面积 / μm²','位置 / μm'],
                               [[q.get('kind','封装边缘线距不足'),q['layer'],[i+1 for i in q['indices']],fmt(q['gap_um']),fmt(q['required_um']),fmt(q.get('intrusion_area_um2',0)),q['point']] for q in edge[:100]]))

    hidden={'per_die_enabled','die_bounds','die_pivots','input_nsigma','interdie','fixed_via_assignment','residual_budget_um','transition_length_um','max_route_angle_deg','routing_edge_keepout_um'}
    appendix='<h2>附录：输入与几何明细</h2>'+grid(['参数','含义/单位','值'],[[key,PARAMETER_DESCRIPTIONS.get(key,key),fmt(value,8)] for key,value in r['parameters'].items() if key not in hidden])
    appendix+=grid(['图层','图形编号','归属问题','可达DIE'],[[q['layer'],q['index'],q['reason'],q['dies']] for q in con.get('ownership_notes',[])])
    positional=con.get('positional_ownership',[])
    if positional:
        appendix+=grid(['图层','图形编号','按位置归属','到该DIE的距离 / μm','归属依据'],
                       [[q['layer'],q['index'],f"DIE{q['die']}",fmt(q['distance_um']),
                         '同时连接多颗DIE，取最近' if q.get('mode')=='shared' else '完全没有连接，取最近'] for q in positional[:100]])
    if len(positional)>100:appendix+=f"<p>按位置归属的运动图形共{len(positional)}个，报告列出前100个。</p>"
    appendix+=grid(['图层','随动设置'],list((motions or r.get('motions',{})).items()))
    appendix+=grid(['层/类型','原图形编号','忽略原因','名义边界'],[[q['layer'],q['index'],q['reason'],q['bounds']] for q in r.get('ignored_geometry',[])])
    html='<html><head><style>body{font-family:sans-serif;color:#263d52;}td,th{border-color:#cfdae5;}h3{color:#345b7b;}</style></head><body><h2>报告：输入 → 公式 → 输出</h2>'+'<p align="center" style="color:#9bb8cd;font-size:20pt">↓</p>'.join(blocks)+appendix+'</body></html>'
    return html,m.resources
