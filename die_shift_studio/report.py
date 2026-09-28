"""Auditable V12 calculation report: bounded inputs, probabilities, geometry."""
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
         [r'\sigma_x=\frac{B_x}{k},\quad\sigma_y=\frac{B_y}{k},\quad\sigma_\theta=\frac{B_\theta\pi}{180k}',
          r'\Sigma_{11}=\sigma_x^2,\quad\Sigma_{22}=\sigma_y^2,\quad\Sigma_{33}=\sigma_\theta^2',
          r'\Sigma_{12}=\Sigma_{21}=\rho\sigma_x\sigma_y,\quad\Sigma_{13}=\Sigma_{23}=\Sigma_{31}=\Sigma_{32}=0',
          r'\mathcal{H}=\{z:|z_j-\mu_j|\leq B_j\},\quad Z=\int_{\mathcal{H}}f_N(z;\mu,\Sigma)\,dz',
          r'f_T(z)=\frac{f_N(z;\mu,\Sigma)\mathbf{1}_{\mathcal{H}}(z)}{Z}'] if math.isfinite(p.target_nsigma) else [r'I=\mathbf{1}\{\mathrm{current\ geometry\ contacts}\}'],
         f'截断前σx={math.sqrt(cov[0,0]):.8g} μm；σy={math.sqrt(cov[1,1]):.8g} μm；σθ={math.sqrt(cov[2,2]):.8g} rad；Z={Z:.12g}' if math.isfinite(p.target_nsigma) else '∞（无穷大符号）为当前几何二值模式，空白行检查名义位置，不表示无界正态尾部',
         'k只能选择1～12或∞。B是围绕系统均值μ的最大随机偏移幅度；μ=0时DX/DY绝不超出±B。σ是截断前母正态尺度，截断后的实际标准差通常更小。Σ是母协方差，ρ为母X/Y相关系数，角度与平移独立；fN为母正态密度，H为允许状态盒，Z为盒内母概率，fT为归一化后密度。相关X/Y联合截断，不逐轴裁剪样本。零方差按确定值积分。∞模式不估计随机概率，概率图只显示名义实体覆盖。')
    step('输入单边幅度的极端预演','每颗DIE的最大X/Y/转角幅度与均值',
         [r'z_j=\mu_j+\varepsilon_jB_j,\quad\varepsilon_j\in\{-1,1\}',
          r'\mu_j-B_j\leq z_j\leq\mu_j+B_j'],
         '每颗芯片均包含8种三参数端点叠加；补充各DIE不利方向、芯片对和封装边界方向，合并重复行',
         'z=(DX,DY,θ)，ε为端点符号。输入35 μm、均值0时平移端点为±35 μm；过孔实际位移包含旋转，可大于35 μm。代表列表不穷举所有多DIE笛卡尔积，连续概率判定独立于代表列表。蓝灰虚线包络包含输入盒的连续刚体位置，不是等概率线。手动输入可超出最大幅度作指定工况检查，但不属于该概率模型支持范围。')
    step('刚体偏移与控制区域',f'最小包围a={p.annulus_um:g} μm；固定线距s={p.line_space_um:g} μm',
         [r'x^{\prime}=C_x+D_x+(x-C_x)\cos\theta-(y-C_y)\sin\theta',
          r'y^{\prime}=C_y+D_y+(x-C_x)\sin\theta+(y-C_y)\cos\theta',
          r'V_a=T_z(V)\oplus\mathcal{B}_a,\quad V_s=T_z(V)\oplus\mathcal{B}_{a+s}',
          r'I_i(z)=\mathbf{1}\{\min_{j\in\mathcal{F}_i}\operatorname{dist}(T_z(V_i),F_j)\leq a+s\}'],
         '包围区为青绿虚线，线距控制区为紫色虚线；红圈标接触点，半透明红色填充真实侵入交集',
         'C为连接芯片中心，x/y为原顶点，D为平移，θ以rad计算；T为刚体变换，V为真实过孔实体，⊕是Minkowski和，Br为半径r圆盘。Fi集合为相接固定RDL平面上全部其他几何网络图形，排除本网络。dist为最短距离；相切也违规，即使侵入面积为0。灰虚线接到当前运动后的孔中心。动画每帧重算控制区、接触点和交集。')
    step('默认行概率判定与预演二值判定','有限k；允许上限α；当前接口及其全部其他网络',
         [r'\alpha=2\Phi(-k),\quad p_i=\int_{\mathcal{H}}I_i(z)f_T(z)\,dz',
          r'p_i>\alpha\ \Longrightarrow\ \mathrm{insufficient}',
          r'I_i(z_{\mathrm{preview}})=1\ \Longrightarrow\ \mathrm{insufficient}'],
         f"允许接触概率={r['target_failure']:.8g}；不足记录{len(r['issues'])}条（含各方案）；待细化接口{len(analysis.get('unresolved',[]))}个" if math.isfinite(p.target_nsigma) else f"名义位置二值接触{len(r['issues'])}条（含各方案）",
         'Φ为标准正态CDF，α为每个接口允许的联合接触概率，不是各网络对概率之和，也不是已验证整包失效率。6σ的α约1.973×10⁻⁹。有限k空白行仅列已证明pi&gt;α的不足；具体极端/手动预演和∞一律按当前几何0/100%判断。目标覆盖级别变化时自动后台更新统计结果并保留预演；其他工艺输入保留上次评估，预演使用当前参数。')
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
    step('过孔实体覆盖概率',f'方法={cfg.probability_method}；样本数=2^{cfg.sample_exponent}；积分阶数={cfg.quadrature_order}；网格={cfg.probability_cell_um:g} μm（0自动）',
         [r'p_V(q)=\int_{\mathcal{H}}\mathbf{1}\{q\in T_z(V)\}f_T(z)\,dz',
          r'\widehat p_{V,G}=\frac{1}{N}\sum_{n=1}^{N}\frac{|G\cap T_{z_n}(V)|}{|G|},\quad\int p_V(q)\,dq=|V|',
          r'\widehat p_{V,G}\simeq\sum_j\omega_j(H*K_j)_G',
          r'\omega_j=\frac{w_j}{2},\quad\theta_j=\mu_\theta+\sigma_\theta\Phi^{-1}\!\left(\Phi(-k)+\frac{h_j+1}{2}[\Phi(k)-\Phi(-k)]\right)'],
         '半透明热图表示整孔覆盖概率；输入最大幅度包络独立绘制且跟随原层显隐',
         'q为空间位置，G为网格，|·|为面积，N为样本数。Monte Carlo/Sobol采用同一相关母分布并联合拒绝超界状态；Sobol拒绝后仅为准随机近似，不沿用原平衡保证。确定性方法用[-1,1] Gauss–Legendre节点hj/权重wj，通过截断正态逆CDF映射得到θj；ω为角度积分权重（和为1），H为相关平移落入每格的条件概率，Kj为该朝向完整孔实体面积掩模，*为卷积。全部平移格覆盖硬输入范围并按Zxy归一化。面积积分应接近孔面积，格值之和不归一为1；角度/栅格/FFT有有限数值精度，可加密复算。低概率尾部可能没有采样命中或低于FFT有效精度，不伪造颜色补齐。极端包络外不可达，包络内也并非处处有可见概率。多个孔透明叠色不是联合覆盖率。')
    step('Space Map：所需线距',f'固定线距s={p.line_space_um:g} μm；连接归属；母协方差；芯片和扇出域',
         [r'A=P-C,\quad b=R(\mu_\theta)A,\quad J_{11}=J_{22}=1,\quad J_{12}=J_{21}=0',
          r'J_{13}=-b_y,\quad J_{23}=b_x',
          r'k_M=\sqrt{F^{-1}_{\chi^2_3}\!\left(1-\frac{\alpha Z}{M}\right)}',
          r'Q_N=\|\mu_{xy}+b-A\|+k_M\sqrt{\lambda_{\max}(J\Sigma J^T)}+\frac{\|A\|(k_M\sigma_\theta)^2}{2}',
          r'Q_H=\|\mu_{xy}+b-A\|+\sqrt{B_x^2+B_y^2}+2\|A\|\sin\!\left(\frac{\min(\pi,B_\theta)}{2}\right)',
          r'S(P)=s+\min(Q_N,Q_H)'],
         '整DIE及其外部随动金属显示需求；整包模式其他区域为基础线距；∞模式仅显示基础线距',
         'P为原始位置（μm），C为所属DIE中心，R为二维旋转矩阵，A/b为原/均值旋转相对坐标，J为位移Jacobian，λmax为2×2协方差矩阵最大特征值；其计算为(v11+v22+√((v11−v22)²+4v12²))/2。M为DIE数，F为3自由度卡方CDF（代码用逆生存函数避免消减）；Z为截断归一化质量。QN为条件置信包络，QH为硬输入范围的确定性上界，Bθ此处用rad。取两者较小仍是保守上界。S不包含孔半径或包围量，不直接用来替代接触概率判定。∞没有统计补偿量。')
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
         '只在真实动孔—固定RDL接口适配；当前状态排除实际接触孔；接受合法型变后重新检查并迭代释放接口。统计风险不作为具体状态的永久冻结条件。每个显示帧重新校验；通过候选与带DRC最佳候选明确区分，拓扑非法才保留源图。深色表示运动与适配部分，适配段有虚线描边。',
         'P为固定RDL原位置，ci为运动孔原中心（区别于所属DIE中心Cd）；G为原固定铜区，Vi为运动过孔；先用Li补足名义所需孔盘得到G0。a为包围量，δ为源DBU，圆弧每象限32段；ra补偿内接弦和量化误差。ri从Li顶点到孔中心的最大距离加2δ求得，不再附加两倍线宽。包围是型变后输出约束，不以源孔盘不足拒绝适配。L为候选过渡宽度，以最大孔中心行程的2倍、固定线宽的2倍和5 μm三者最大值为基数，依次乘1、1.6、2.6、4.2、6.8、11，选择首个全部约束通过的最短候选；不是全局最优布线证明。wi为该孔权重，Wd把同DIE的多个孔光滑合并，Td为真实刚体变换，Dd为平移，R为旋转矩阵。贝塞尔方式混合位移，圆弧方式沿原点到刚体目标点间的圆弧行进，圆心角γ由设置的度数换成rad，正负随该DIE转角（零角取正）；A/B为弦向及垂向系数，J90将向量逆时针旋转90°。只有平移时两方式也不同。C1/C2指位移场在刚体区、固定区交界的一阶/二阶导数连续；铜内角低于设置时尝试扩大局部圆角，并重复全套校验；原GDS折角不被承诺全部变光滑，离散多边形也不声称数学上严格C2。无折叠充分界、固定连接端不动、原图与型变后最小线宽、异网及其他动孔控制区线距、包围、封装、禁布区和拓扑均校验。曲线自适应细分后按源DBU量化；宽度/线距使用近似平行（≤5°）的非相邻边对规则，实际短接不受角度过滤；长度阈值向上取整到DBU，不放宽。型变结果只作当前工况预演，不修改原GDS，也不改变前述原图统计概率。')
    step('三段追溯与全局DRC','来源铜连通区、运动/固定过孔；最小线宽线距、连续性与内角设置',
         [r'S=L_0\left(\frac{w_0}{w_{\min}}\right)^{1/4},\quad g=\max(w_0,w_{\min}),\quad L_{\max}=L_0-2g',
          r't(P)=\min\!\left(1,\max\!\left(0,\frac{(P-b)\cdot u}{L}\right)\right)',
          r'\phi=\arccos\!\left(\frac{|e_1\cdot e_2|}{\|e_1\|\,\|e_2\|}\right),\quad\phi\leq5^{\circ}',
          r'Q=\left(\sum_{j=1}^{m}A_j,\ m,\ \operatorname{Area}(G^{\prime}\bigtriangleup G)\right)'],
         '局部孔盘调整无通过结果时，沿实际连通铜图形寻找运动段、适配段、固定段；固定端不动。独立DRC开关控制粉色违规显示，关闭开关不改变判定。',
         'L0/w0是候选对边的共同长度/间距；S为长宽排序值，g为两端避开拐角的保留长度，Lmax为可用长度，wmin为固定最小线宽。先居中取Lmax，再尝试0.75/0.5倍及偏中心位置，最多48个组合。b为适配起点，u为指向固定端的单位向量，L为适配长度，P为源点；运动段W=1、固定段W=0，适配段使用前述C1/C2权重与贝塞尔/圆弧公式。e1/e2为两条非零边的方向；只检查近似平行、非共享顶点的边，粉色定位真实不足段；实际异网相交始终算短接。没有完全合格结果时，对合法拓扑候选按Q字典序选取：Aj为第j组违规区域面积（μm²），m为违规组数，最后一项为改动铜面积；这是有限搜索中的最佳候选，不是合格解或全局最优证明。最终再次检查所有显示候选之间的冲突。交互帧复核已有结构，停止操作后完整搜索；所有标记与图形用同一当前状态。')
    step('三方案比较：实图条件与待验证能力','相同真实运动过孔—固定RDL接口；实际芯片边长、层数、线宽线距；自动跨芯片网络',
         [r'C_b=L\max\!\left(0,\left\lfloor\frac{\ell_b+s}{w+s}\right\rfloor\right),\quad\eta_b=\frac{N_b}{C_b}',
          r'm_i=\operatorname{dist}(c_i,\partial F_i)-r_i-a,\quad\eta_{\mathrm{capture}}=\max_i\frac{Q(c_i)}{m_i}'],
         r['recommendation']['name']+'；'+r['recommendation']['reason'],
         'Cb为芯片边b的轨道容量上界；L为对应RDL层数，ℓb为边长，w/s为对应线宽/线距，Nb为按最近边分配的实际接口数量。ci为接口中心，Fi为本网络接收图形，ri为孔相对自身中心最大顶点距离，mi为几何捕获裕量（中心不在Fi内取距离0）；分母≤0表示无捕获裕量。方案1比较轨道容量，方案2比较实际捕获裕量且自动排除跨DIE刚体冲突，方案3比较捕获/容量两条适配路径并要求运动RDL。运动层分类不证明晶圆段预制。已删除人工补偿长度、斜角、残差及边端预留；动态重布线可行性仍需真实路由确认。机械净距另检查。Γ汇总必要条件和已确认概率超限比，不把未定概率当通过；所有推荐均为初筛。')
    blocks.append(grid(['方案','状态','Γ','理由'],[[s['scheme'],s['status'],fmt(s['gamma']),'; '.join(s['reasons'])] for s in r['schemes']]))
    if p.per_die_enabled:
        step('逐芯片输入解析','GDS识别的DIE编号与独立单边幅度',
             [r'\sigma_{x,d}=\frac{B_{x,d}}{k},\quad\sigma_{y,d}=\frac{B_{y,d}}{k},\quad\sigma_{\theta,d}=\frac{\pi B_{O,d}}{180k}'],
             '每颗芯片均使用自己的幅度；前述单组数值示例为DIE 1，几何连通归属决定实际调用哪组。',
             'd为DIE编号；Bx,d和By,d是μm单边最大幅度，BO,d是度，k为目标级别。有限k采用截断联合正态，∞为名义位置二值判断。')
        blocks.append(grid(['DIE','Bx / μm','By / μm','BO / °','σx / μm','σy / μm','σθ / rad'],
            [[i+1,*v,v[0]/p.target_nsigma,v[1]/p.target_nsigma,math.radians(v[2])/p.target_nsigma] for i,v in enumerate(p.die_bounds)]))
    adaptive=r.get('adaptive_preview')
    if adaptive:
        if not adaptive.get('statistics_match_saved_evaluation',True):blocks.append('<p>本表使用当前预演参数，与上次方案评估快照不同；重新点击方案评估可同步。目标σ='+h(fmt(adaptive['target_sigma']))+'；包围/固定线宽/固定线距（μm）='+h(str([adaptive['annulus_um'],adaptive['line_width_um'],adaptive['line_space_um']]))+'；逐芯片幅度（μm、μm、°）='+h(str(adaptive['die_bounds']))+'</p>')
        blocks.append('<h3>预演分析的迭代结果</h3><p>'+h(adaptive['continuous_probability'])+'</p>'+grid(['代表状态','初始不足','最终不足','释放','迭代轮数'],[[q['name'],q['initial'],q['remaining'],q['released'],len(q['iterations'])] for q in adaptive['states']]))
    hidden={'per_die_enabled','die_bounds','input_nsigma','interdie','fixed_via_assignment','residual_budget_um','transition_length_um','max_route_angle_deg','routing_edge_keepout_um'}
    appendix='<h2>附录：输入与几何明细</h2>'+grid(['参数','含义/单位','值'],[[key,PARAMETER_DESCRIPTIONS.get(key,key),fmt(value,8)] for key,value in r['parameters'].items() if key not in hidden])
    appendix+=grid(['图层','图形编号','归属问题','可达DIE'],[[q['layer'],q['index'],q['reason'],q['dies']] for q in con.get('ownership_notes',[])])
    appendix+=grid(['图层','随动设置'],list((motions or r.get('motions',{})).items()))
    appendix+=grid(['层/类型','原图形编号','忽略原因','名义边界'],[[q['layer'],q['index'],q['reason'],q['bounds']] for q in r.get('ignored_geometry',[])])
    html='<html><head><style>body{font-family:sans-serif;color:#263d52;}td,th{border-color:#cfdae5;}h3{color:#345b7b;}</style></head><body><h2>报告：输入 → 公式 → 输出</h2>'+'<p align="center" style="color:#9bb8cd;font-size:20pt">↓</p>'.join(blocks)+appendix+'</body></html>'
    return html,m.resources
