"""Auditable V17 calculation report: bounded inputs, probabilities, geometry."""
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
    con=r['connections'];analysis=r.get('contact_analysis',{});_,bounds=mean_and_bounds(p.for_die(0),count);Z,Zerr=normalizer(p.for_die(0),count)
    step('GDS、金属层序与几何修复',r['top_cell']+'；上→下：'+' → '.join(con['layer_order']),[],
         f"{count}颗芯片；{con['contact_count']}对相邻层连接；{analysis.get('network_count',0)}个几何网络；修复{len(r.get('repaired_geometry',[]))}个，忽略{len(r.get('ignored_geometry',[]))}个图形",
         '长度统一为μm。边框、不参与和禁布层不建立电连接；同物理层号的datatype共用一个金属平面。Merge修复保留孔洞，失败图形用玫红细虚线框出并忽略，不改源GDS。')
    step('连接、芯片归属与跨芯片自动识别','同层接触与相邻金属层重叠；最下层与DIE的实际接触根',
         [r'E_{ij}=\mathbf{1}\{\operatorname{Area}(A_i\cap B_j)>\epsilon_A\},\quad\epsilon_A=\max(10^{-12},0.01\delta^2)',
          r'\mathcal{D}(A)=\bigcup_{B\in\mathrm{down}(A)}\mathcal{D}(B)'],
         f"运动归属待确认{len(con.get('ownership_notes',[]))}个；跨芯片几何网络{len(r.get('interdie_networks',[]))}个",
         'A、B为相邻层图形，δ为GDS网格（μm），εA为面积容差（μm²）。同层相连先合并，再沿下层追溯D(A)芯片集合；唯一根允许随动，多根/无根待确认。全电连接分量包含两个以上DIE根即跨芯片网络，不需要手动指定。支持过孔—过孔、过孔—RDL等连接。只评估运动过孔与相邻固定RDL的真实名义接口。芯片外扇出沿连接归属，绝不按最近芯片分配。')
    step('最大输入幅度与统一统计模型',f'Bx={bounds[0]:g} μm；By={bounds[1]:g} μm；Bθ={p.bound_angle_deg:g}°；目标={fmt(p.target_nsigma)}',
         [r'\sigma_x=\frac{B_x}{\kappa},\quad\sigma_y=\frac{B_y}{\kappa},\quad\sigma_\theta=\frac{B_\theta\pi}{180\kappa}',
          r'\Sigma_{11}=\sigma_x^2,\quad\Sigma_{22}=\sigma_y^2,\quad\Sigma_{33}=\sigma_\theta^2',
          r'\Sigma_{12}=\Sigma_{21}=\rho\sigma_x\sigma_y,\quad\Sigma_{13}=\Sigma_{23}=\Sigma_{31}=\Sigma_{32}=0',
          r'\mathcal{H}=\{z:|z_j-\mu_j|\leq B_j\},\quad Z=\int_{\mathcal{H}}f_N(z;\mu,\Sigma)\,dz',
          r'f_T(z)=\frac{f_N(z;\mu,\Sigma)\mathbf{1}_{\mathcal{H}}(z)}{Z}'] if math.isfinite(p.target_nsigma) else [r'I=\mathbf{1}\{\mathrm{current\ geometry\ contacts}\}'],
         f'截断前σx={math.sqrt(cov[0,0]):.8g} μm；σy={math.sqrt(cov[1,1]):.8g} μm；σθ={math.sqrt(cov[2,2]):.8g} rad；Z={Z:.12g}' if math.isfinite(p.target_nsigma) else '∞（无穷大符号）为当前几何二值模式，空白行检查名义位置，不表示无界正态尾部',
         'k可选择0～12或∞。公式中的尺度除数使用κ：非零有限k时κ=k，0时κ沿用最近有限级别（首次6）；0级α=0且不省略有界支持内尾部。B是围绕系统均值μ的最大随机偏移幅度；μ=0时DX/DY绝不超出±B。σ是截断前母正态尺度，截断后的实际标准差通常更小。Σ是母协方差，ρ为母X/Y相关系数，角度与平移独立；fN为母正态密度，H为允许状态盒，Z为盒内母概率，fT为归一化后密度。相关X/Y联合截断，不逐轴裁剪样本。零方差按确定值积分。∞模式不估计随机概率，未勾选覆盖范围时概率图只显示名义实体；勾选后显示按保留尺度计算的0级分布。')
    step('输入单边幅度的极端预演','每颗DIE的最大X/Y/转角幅度与均值',
         [r'z_j=\mu_j+\varepsilon_jB_j,\quad\varepsilon_j\in\{-1,1\}',
          r'\mu_j-B_j\leq z_j\leq\mu_j+B_j'],
         '每颗芯片均包含8种三参数端点叠加；补充各DIE不利方向、芯片对和封装边界方向，合并重复行',
         'z=(DX,DY,θ)，ε为端点符号。输入35 μm、均值0时平移端点为±35 μm；过孔实际位移包含旋转，可大于35 μm。代表列表不穷举所有多DIE笛卡尔积，连续概率判定独立于代表列表。可选的覆盖范围虚线包含相应连续刚体位置，不是等概率线；默认不绘制外包络。手动输入可超出最大幅度作指定工况检查，但不属于该概率模型支持范围。')
    step('逐芯片旋转中心解析','共用或逐DIE旋转中心选项；GDS芯片边框；自定义相对坐标',
         [r'C=C_{\mathrm{die}}+c_{\mathrm{relative}}',r'T_z(P)=C+R(\theta)(P-C)+D'],
         '；'.join(f"DIE {row['die']}：{row.get('rotation_origin_mode','center')}，旋转中心{row.get('rotation_origin_um')} μm" for row in r['dies']),
         'Cdie为名义芯片最小外接矩形中心；正中心时crelative=(0,0)，角点取该矩形的实际顶点（按图纸Y分为上下两点，再按X分左右），自定义按图纸X/Y轴输入相对Cdie的μm坐标。C始终为源图参考点，不从偏移后图形重新求取。D=(DX,DY)为用户输入平移，R为逆时针旋转矩阵；芯片自身中心、边框、扇出线路和过孔均用同一C。')
    step('刚体偏移与控制区域' ,f'最小包围a={p.annulus_um:g} μm；固定线距s={p.line_space_um:g} μm',
         [r'x^{\prime}=C_x+D_x+(x-C_x)\cos\theta-(y-C_y)\sin\theta',
          r'y^{\prime}=C_y+D_y+(x-C_x)\sin\theta+(y-C_y)\cos\theta',
          r'V_a=T_z(V)\oplus\mathcal{B}_a,\quad V_s=T_z(V)\oplus\mathcal{B}_{a+s}',
          r'I_i(z)=\mathbf{1}\{\min_{j\in\mathcal{F}_i}\operatorname{dist}(T_z(V_i),F_j)\leq a+s\}'],
         '包围区为青绿虚线，线距控制区为紫色虚线；红圈标接触点，半透明红色填充真实侵入交集',
         'C为所属DIE的实际旋转中心，x/y为原顶点，D为平移，θ以rad计算；T为刚体变换，V为真实过孔实体，⊕是Minkowski和，Br为半径r圆盘。Fi集合为相接固定RDL平面上全部其他几何网络图形，排除本网络。dist为最短距离；相切也违规，即使侵入面积为0。灰虚线接到当前运动后的孔中心。动画每帧重算控制区、接触点和交集。')
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
    step('过孔实体覆盖概率',f'方法={cfg.probability_method}；样本数=2^{cfg.sample_exponent}；积分阶数={cfg.quadrature_order}；网格={cfg.probability_cell_um:g} μm（0自动）',
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
    step('预演分析：来源图形的局部连续型变',f'连续性={cfg.deformation_continuity}；方式={cfg.deformation_method}；曲线误差={cfg.deformation_tolerance_um:g} μm；固定线宽w={p.line_width_um:g} μm、线距s={p.line_space_um:g} μm',
         [r'r_a=\frac{a}{\cos(\pi/128)}+2\delta,\quad L_i=V_i\oplus\mathcal{B}_{r_a},\quad G_0=G\cup\bigcup_i L_i',
          r'r_i=\max_{x\in\mathrm{vertices}(L_i)}\|x-c_i\|+2\delta',
          r't_i(P)=\min\!\left(1,\max\!\left(0,\frac{\|P-c_i\|-r_i}{L}\right)\right)',
          r'w_{C1}(t)=1-3t^2+2t^3,\quad w_{C2}(t)=1-10t^3+15t^4-6t^5',
          r'W_d(P)=1-\prod_{i\in d}(1-w_i(P))',
          r'\Psi_B(P)=P+\sum_d W_d(P)[T_d(P)-P]',
          r'A(w)=\frac{1-\cos(\gamma w)+\cot(\gamma/2)\sin(\gamma w)}{2}',
          r'B(w)=\frac{\cot(\gamma/2)[1-\cos(\gamma w)]-\sin(\gamma w)}{2}',
          r'\Psi_A(P)=P+\sum_d[A(W_d)\Delta_d+B(W_d)J_{90}\Delta_d],\quad\Delta_d=T_d(P)-P'],
         '只在真实动孔—固定RDL接口适配；每个有位移的接口都尝试，接受合法型变后重新检查并迭代释放接口。统计风险不作为具体状态的永久冻结条件。每个显示帧重新校验；只显示全部约束通过的候选，最终全层DRC未通过则恢复原图。深色表示运动与适配部分，适配段有虚线描边。',
         'P为固定RDL原位置，ci为运动孔原中心（区别于所属DIE中心Cd）；G为原固定铜区，Vi为运动过孔；先用Li补足名义所需孔盘得到G0。a为包围量，δ为源DBU，圆弧每象限32段；ra补偿内接弦和量化误差。ri从Li顶点到孔中心的最大距离加2δ求得，不再附加两倍线宽。包围是型变后输出约束，不以源孔盘不足拒绝适配。L为候选过渡宽度，以最大孔中心行程的2倍、固定线宽的2倍和5 μm三者最大值为基数，按设置的递增倍率g生成L=L0g^j，j=0至搜索级数减1，选择首个全部约束通过的最短候选；不是全局最优布线证明。wi为该孔权重，Wd把同DIE的多个孔光滑合并，Td为真实刚体变换，Dd为平移，R为旋转矩阵。贝塞尔方式混合位移，圆弧方式沿原点到刚体目标点间的圆弧行进，圆心角γ由设置的度数换成rad，正负随该DIE转角（零角取正）；A/B为弦向及垂向系数，J90将向量逆时针旋转90°。只有平移时两方式也不同。C1/C2指位移场在刚体区、固定区交界的一阶/二阶导数连续；铜内角低于设置时尝试扩大局部圆角，并重复全套校验；原GDS折角不被承诺全部变光滑，离散多边形也不声称数学上严格C2。无折叠充分界、固定连接端不动、按带符号容差得到的型变后最小线宽和固定铜异网线距、按工艺原值计算的其他动孔控制区、包围、封装、禁布区和拓扑均校验。曲线自适应细分后按源DBU量化；宽度/线距使用近似平行（≤5°）的非相邻边对规则，实际短接不受角度过滤；有效长度阈值向上取整到DBU；不在用户容差之外额外放宽。型变结果只作当前工况预演，不修改原GDS，也不改变前述原图统计概率。')
    step('方向性孔盘、无折叠证明与逐帧动画','实际铜区域、补足包围量后的运动孔盘、固定过孔、用户连续性与插值设置',
         [r't=\min(1,\max(0,(P-b)\cdot u/L)),\quad\Psi(P)=P+w(t)[T(P)-P]',
          r'J_{11}>0,\quad J_{22}>0,\quad\det J>0'],
         '短宽孔盘增加方向性过渡；径向场按相交支撑区计算充分界。所有接受结果继续检查完整固定RDL，失败恢复原图。',
         'b为过渡起点，u为指向固定端的单位向量，L为过渡长度；J为局部坐标中Ψ的雅可比矩阵，Jij=∂Ψi/∂Pj；运动端为补足包围量后的整个孔盘，不只包含裸孔。方向性局部候选从实际两端间隙生成，不要求圆形支撑区绕开固定端。贝塞尔权重的全截面场在覆盖整个源铜的局部坐标矩形上，对两个主对角元及行列式建立解析正下界（严格大于0.02），满足P矩阵充分条件才放行；否则仍拒绝。必要时尝试峰值斜率1.25的分段C2积分权重，避免把较大的横向剪切误判为折叠。此证明只排除映射折叠，不替代线宽线距验收。细分仅作用于型变影响边，固定或刚体边保持原顶点；候选本征DRC按几何指纹复用，异网检查仅截取规则距离外加保护边距的相关区域。动画先构造并校验每个中间状态，计算时间与播放时间分开；算完连续播放和边算边显示均支持，相同条件复用完整动画缓存。只显示已完成检查的帧；未通过的型变线路恢复原图并标记。不在两张铜图之间插值生成未检查形状。')
    step('三段追溯与全局DRC','来源铜连通区、运动/固定过孔；最小线宽线距、连续性与内角设置',
         [r'S=L_0\left(\frac{w_0}{w_{\min}}\right)^{1/4},\quad g=\beta_g\max(w_0,w_{\min}),\quad L_{\max}=L_0-2g',
          r't(P)=\min\!\left(1,\max\!\left(0,\frac{(P-b)\cdot u}{L}\right)\right)',
          r'\phi=\arccos\!\left(\frac{|e_1\cdot e_2|}{\|e_1\|\,\|e_2\|}\right),\quad\phi\leq5^{\circ}',
          r'Q=\left(\sum_{j=1}^{m}A_j,\ m,\ \operatorname{Area}(G^{\prime}\bigtriangleup G)\right)'],
         '局部孔盘调整无通过结果时，沿实际连通铜图形寻找运动段、适配段、固定段；固定端不动。独立DRC开关控制粉色违规显示，关闭开关不改变判定。',
         'L0/w0是候选对边的共同长度/间距；S为长宽排序值，βg为设置的拐角端部预留倍数，g为两端避开拐角的保留长度，Lmax为可用长度，wmin为固定最小线宽。先居中取Lmax，再尝试0.75/0.5倍及偏中心位置，候选预算由设置控制；同时增加完整投影截面法处理弯曲、宽孔盘及分支，截面前为运动段、后为固定段。设置允许关闭这类通用截面。b为适配起点，u为指向固定端的单位向量，L为适配长度，P为源点；运动段W=1、固定段W=0，适配段使用前述C1/C2权重与贝塞尔/圆弧公式。e1/e2为两条非零边的方向；只检查近似平行、非共享顶点的边，粉色定位真实不足段；实际异网相交始终算短接。没有完全合格结果时，对合法拓扑候选按Q字典序选取：Aj为第j组违规区域面积（μm²），m为违规组数，最后一项为改动铜面积；这是有限搜索中的最佳候选，不是合格解或全局最优证明。Q仅用于内部搜索排序；不合格候选不会显示。最终再次检查所有候选与候选、候选与恢复原图之间的冲突，迭代回退至稳定。交互帧复核已有结构，停止操作后完整搜索；所有标记与图形用同一当前状态。')
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
    if p.per_die_enabled:
        step('逐芯片输入解析','GDS识别的DIE编号与独立单边幅度',
             [r'\sigma_{x,d}=\frac{B_{x,d}}{\kappa},\quad\sigma_{y,d}=\frac{B_{y,d}}{\kappa},\quad\sigma_{\theta,d}=\frac{\pi B_{O,d}}{180\kappa}'],
             '每颗芯片均使用自己的幅度；前述单组数值示例为DIE 1，几何连通归属决定实际调用哪组。',
             'd为DIE编号；Bx,d和By,d是μm单边最大幅度，BO,d是度，k为目标级别。有限k采用截断联合正态，∞为名义位置二值判断。')
        blocks.append(grid(['DIE','Bx / μm','By / μm','BO / °','σx / μm','σy / μm','σθ / rad'],
            [[i+1,*v,v[0]/p.scale_nsigma,v[1]/p.scale_nsigma,math.radians(v[2])/p.scale_nsigma] for i,v in enumerate(p.die_bounds)]))
    adaptive=r.get('adaptive_preview')
    if adaptive:
        if not adaptive.get('statistics_match_saved_evaluation',True):blocks.append('<p>本表使用当前预演参数，与上次方案评估快照不同；重新点击方案评估可同步。目标σ='+h(fmt(adaptive['target_sigma']))+'；包围/固定线宽/固定线距（μm）='+h(str([adaptive['annulus_um'],adaptive['line_width_um'],adaptive['line_space_um']]))+'；逐芯片幅度（μm、μm、°）='+h(str(adaptive['die_bounds']))+'</p>')
        blocks.append('<h3>预演分析的迭代结果</h3><p>'+h(adaptive['continuous_probability'])+'</p>'+grid(['代表状态','初始不足','最终不足','释放','迭代轮数'],[[q['name'],q['initial'],q['remaining'],q['released'],len(q['iterations'])] for q in adaptive['states']]))
    hidden={'per_die_enabled','die_bounds','die_pivots','input_nsigma','interdie','fixed_via_assignment','residual_budget_um','transition_length_um','max_route_angle_deg','routing_edge_keepout_um'}
    appendix='<h2>附录：输入与几何明细</h2>'+grid(['参数','含义/单位','值'],[[key,PARAMETER_DESCRIPTIONS.get(key,key),fmt(value,8)] for key,value in r['parameters'].items() if key not in hidden])
    appendix+=grid(['图层','图形编号','归属问题','可达DIE'],[[q['layer'],q['index'],q['reason'],q['dies']] for q in con.get('ownership_notes',[])])
    appendix+=grid(['图层','随动设置'],list((motions or r.get('motions',{})).items()))
    appendix+=grid(['层/类型','原图形编号','忽略原因','名义边界'],[[q['layer'],q['index'],q['reason'],q['bounds']] for q in r.get('ignored_geometry',[])])
    html='<html><head><style>body{font-family:sans-serif;color:#263d52;}td,th{border-color:#cfdae5;}h3{color:#345b7b;}</style></head><body><h2>报告：输入 → 公式 → 输出</h2>'+'<p align="center" style="color:#9bb8cd;font-size:20pt">↓</p>'.join(blocks)+appendix+'</body></html>'
    return html,m.resources
