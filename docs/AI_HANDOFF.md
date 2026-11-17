# V49 软件交接（验证中）

新增入口：`ruler.py/measurement_data.py`测量和纯数据持久化；`manual_regions.py`严格旋转框；`parallel_zones.py`自动方向与相邻截面；`drc_rules.py`有效规则/生成余量/冲突严重度；`reconstruction_diagnostic.py`失败局部及双侧虚线；`measured_issues.py`完整SQLite问题目录；`partial_output.py`明确局部删铜及量化核验；`oas_errors.py`逐DIE ERROR矩形。`current_drc.locations`是画布、表格和IPC的统一问题组件目录。

最新规范见[V49说明](V49_使用说明.md)。本轮不再适用旧版“实测不检查尺寸DRC”和“缓存次数决定行颜色”；测量与框选字段缺省兼容旧工程。安全范围是仍存活的懒计算功能，不应删除。

V48客户端调度/输出与最终验收见[V48验证](V48_VALIDATION.md)和[V48说明](V48_使用说明.md)。以下V47预演合同完整保留；本轮没有重写其几何算法。

measured_ui在空闲启用/忙时pending、冻结工程及单指令之间区分状态；不能从tick调用run_evaluation。set_data在requested_measured/remote_locked时不恢复结果侧车或自动叠加。Session.ensure_plans只首任务追溯，线程私有求解器共享克隆的名义plan_pool；measured_only路径规划不调用概率candidate_mass。普通交互仍执行完整选定范围。

representatives只生成max(n_i)条同步非零状态，不引入静止活动DIE；零位由调用方加一次。不要把该条件良率说成全输入良率。新增layout高度schema5可选字段layer_table_height；实测设置展开状态用expanded_settings。

GD55两处根因：近重合固定/运动端产生0.37μm虚短路径；多运动孔的逐对候选缺少共用刚体侧偏好。重叠端孔仅从运动学锚点过滤（有其他分离锚点时），不删实际via/FixedRules/topology终端；RoutePlan.choose临时优先共同land，之后恢复单路径land及弧长边界，保持手工跨叉自由。topology_routing最终铜必须避开禁布区，允许共边。

当前验收见[V47验证](V47_VALIDATION.md)。下文保留V46基础代码地图；冲突以V47及最新用户要求为准。

# V46 开发交接

V46最新范围合同：extreme_view.mode经GUI同步到ViewSettings.evaluation_scope；重点不是纯显示。evaluation_scope.py统一所选目录、代表单元权重与条件概率，零位包含在内。focused_reconstruction绕过全联合网格准备，复用直接姿态求解；width的汇总和两项良率共享focus bundle；原铜DRC保存显式已检查姿态，未访问状态必须checked=False。范围加入评估/重构/宽度缓存；切换触发代次更新。全部模式仍是原完整输入算法。

当前分支修复用户新增的nul17123两处问题：首行(8914,5497)真实鼠标圆点触发过GEOS prepared STRtree原生崩溃；(8633,2284)部分姿态把源外周误画成分叉边界。`topology_routing.resolve`用调用私有的查询几何，不能改回跨工作线程共享prepared对象；`branch_regions.interior_interfaces`只提取内部材料分界，裁掉数DBU外周带只影响控制柄，不修改实际铜。混合分叉的线与整孔盘圆点同时保留；点击圆点从真实端点弧长开始。`CutDrag._live`相同吸附状态复用，发布前运行外部案例工具并确认进程退出码。

V46入口变化：`jobs.py`兼容平铺JSON和项目目录；保存图纸副本+相对引用，结果在`Cache/`，运行缓存随项目迁移。`oas_stream.Store`支持阶段专属partial OAS，首次/每64项/内存阶段耐久刷新；最终目标仍全成功后原子替换。阶段文件可独立读取，客户端暂不从几何检查点自动续算，未提交批次仍重放。`ignored_motion.complex_mode`只在整个输入盒得到空间/端孔约束证明后自动选择复杂铜刚体模式；不可证明时保留局部求解。

RealDataAnalyse在另一窗口协作，合并其已提交历史，不能复制未提交工作树或单独丢掉客户端发布。最终说明见V46使用说明，验证见V46_VALIDATION；以下V45基础合同继续适用。

# V45基础行为（V46覆盖项除外）

V45基础UI与计算合同见[V45说明](V45_使用说明.md)，验收见[V45验证](V45_VALIDATION.md)。V44已独立发布，不能回改标签。

新增模块：current_drc.py统一实际铜规则/电连接依赖，drc_overlay.py画布定位，analysis_ui.py延迟任务控制，result_analysis.py概率与安全盒，manual_preview.py逐DIE弹窗。GlobalDRC缓存静态/同刚体检查，extreme_validation加入跨DIE规则因子；改变容差须改变零位帧缓存键。旧report.py/formula_render.py/width_yield_report.py已删除。

注意：A颜色只反映A自身违规，当前DRC表检查全层，两者不相等；V47起批量和交互IPC均按指令跳过完整显示DRC/良率，保留电连接、隔离和禁布区门禁。不要重新加入报告、图形数量扫描或表格专用统计。

# Die Shift Analyze：项目与算法交接

## 项目来由和用途

Die-first封装中，贴片、塑封模流和材料热变形使DIE平移/旋转。软件从GDS/OAS建立几何连接，比较三种重构路径，并预览偏移造成的接触、布线通道风险及固定RDL的适配假设。长度μm、界面角度°、内部rad。它不是签核布线器，也不能以模型良率替代生产数据。

V22导入1102附件形成规范主线；历史版本在Git tag保留。后续逐步增加实体概率、全组合、通道分析、工程节点、离线Windows后端、精确DXF、源绑定动区。V38删除旧预演分析，V42进一步删除“修复补块”；**重构预演**是当前独立功能，不要把已退役算法重新接回来。

## 用户工作流

```mermaid
flowchart LR
 G[GDS与工程节点] --> L[手动用途/层序]
 L --> C[几何连通与DIE归属]
 C --> E[方案评估与完整离散目录]
 E --> P[姿态/极端足迹/避让空间]
 E -->|用户进入分析页| A[三方案/安全盒/模型概率]
 E --> W[宽度分析和联合模型良率]
 E --> R[重构预演与原生规则]
 W --> S[工程结果侧车]
 R --> S
 R --> D[名义零位ABC轮廓DXF]
```

源文件只读。边框、用途、层序由用户设置；显示checkbox只影响画布。金属为运动/固定RDL与运动/固定过孔；非参与和边框不进入金属电连接。Q菜单工程节点改变名义几何，保存指纹，所有计算入口共用编辑后的快照。

## 核心代码地图

| 责任 | 主要文件 |
|---|---|
| 参数/导入/三方案初筛 | core.py、screening.py、gds_merge.py、geometry_errors.py |
| 相邻金属连通与真实DIE归属 | stack.py、contact.py、preview.py |
| 偏移分布/离散目录/积分 | statistics.py、distribution.py、model_yield.py、width_grid.py |
| 条件事件并集与混合进制分页 | extreme_filter.py、reconstruction_events.py |
| 通道、刚性孔盘、原线宽需求 | width_analysis.py、width_summary.py、landing_geometry.py |
| 边距/禁布区 | edge_clearance.py、width_keepout.py、width_yield_geometry.py |
| 路径/名义动静区/分叉合并 | reconstruction.py、reconstruction_route.py、plane_route.py、branch_regions.py、reconstruction_zones.py |
| 边界角度/手动编辑/忽略随动 | reconstruction_angles.py、reconstruction_edits.py、ignored_motion.py、reconstruction_ownership.py |
| 最终原生规则与良率 | reconstruction_rules.py、reconstruction_yield.py、native_backend.py、native_ops.py |
| 工程参数、结果恢复 | project.py、settings.py、project_results.py、gds_merge.py |
| GUI、后台调度、取消 | gui.py、reconstruction_status.py、task_control.py、scheduling.py |
| 交互叠加与缓存 | reconstruction_overlay.py、width_overlay.py、render_cache.py、session_cache.py |
| 导出 | export_ui.py、dxf_export.py、region_export.py、region_contract.py |
| CPU/GPU资源与Windows | resources.py、acceleration.py及CUDA模块、RUN.bat、BUILD.bat、Die_Shift_Analyze.spec |

文件路径均相对die_shift_studio，BAT/spec在根目录。先用rg确认现有入口；不要另建第二份主窗口、模型或导出算法。

## 数学与统计口径

点P相对实际旋转中心C的姿态变换是 `T(P)=C+R(θ)(P−C)+D`，D为XY平移，θ为rad。旋转中心可为DIE中心、四角或用户相对中心偏移，不能用它代替物理DIE边框。

输入幅度B为最大单边随机范围，有限k级使用母尺度σ=B/k，并在输入范围截断。0沿用最近有限尺度且不做级别筛选；∞接触显示为当前实体二值，新联合良率仍用最近有限尺度。零幅度轴锁在0，即使另有系统均值。每个活动轴取0、均值−B、均值+B；所有DIE用混合进制组合，不把对角几组当成全组合。

有限k低概率门限为 `2Φ(−k)`；只有事件上界已证低于门限才淡显/灰显。有限网格零失败不是连续安全证明。连续证明仅覆盖被证明的特殊几何/变换，其他情况保留未知。

三方案初筛以空间需求/可用空间的无量纲指标比较，详见screening与result_analysis；初筛可行不等于布线/DRC可行。只在主动访问结果分析时计算，报告模块已删除。

宽度通道保留实际过孔孔盘及固定障碍，忽略运动网络的普通线作为障碍，但保留其名义贯穿需求。孔盘尺寸刚性；线宽可收窄部分不能跨过孔。通道两端普通壁需线距，封装壁用独立边距c，禁布壁允许共边但不允许正面积侵入。按原宽可通过、仅收窄可通过、最小线宽仍不足分绿/黄/红。两个宽度良率是全修正未忽略红黄和完全不修正，按整包关联事件并集积分，不按问题数量乘独立概率。

## 重构数据模型

名义源路径和端点跨全部姿态共享。内部旧键B是固定、C是移动；公共输出映射恰好相反，统一在region_contract中转换。A始终为适配区。

普通路径按弧长两端切分，短线/拐角/孔盘需有效拓扑。串联区按有效长度分摊位移，不能每段都重复移动完整前缀。碰接区合并，已吸收的身份保存删除记录以免后续编辑复活。所有控制柄为蓝色；整孔盘模式只允许真实相连端孔且需要触发余量。

分叉构造运动端到固定端的路径集合。大网络用覆盖全部端点的集合减少重复路径；从铜中去除候选A域后，剩余连通分量按运动/固定端归属。若一个分量仍同时含运动和固定端，将其纳入A，从而不能有运动—固定路径绕过适配域。相接A域合并；多DIE先做源连通分界，各分支仍使用各自DIE姿态。

多端合并域采用连续位移场连接刚性端，单端普通路径保留四种原有曲线候选。分叉域先把刚性连通块视为节点，以相邻域的间距倒数为权重求解Laplace位移势；运动端为1、固定端为0，中间悬浮块的平移与转角按势值分配。域内部用到相邻刚体的距离倒数权重插值，直线/C1/C2分别使用1/2/3次幂；这不是任意多端区域的精确圆弧布线证明。原生线宽、线距、包围量、连接、封装边距与禁布区检查实际最终铜，失败须保留原因，不能只检查控制点。

A颜色仅由A自身型变前/后的见证决定。两侧铜默认图层颜色；独立违规不污染A，但仍影响全局规则。V43忽略铜默认整块静止，右键显式选择整块随动；同块多DIE刚体矩阵不一致则保留原铜并失败，不再默认按分支运动。这条最新用户合同覆盖V42默认。两端包围量仍检查。

默认边界角度为重构区优先；两侧平均同时考虑两边切向。拖动先给实时几何反馈、后台补完整角度与规则，代次不符不能发布。用户手动角度是源坐标中的绝对方向；移动端点后保持，右键可撤销。

## 缓存和持久状态

运行缓存是有限LRU，读取不增加计数，零位保留，动画可正反复用；控制项、几何、工艺、姿态要进入对应缓存身份。单源编辑复用其他源的型变；全离散目录也按源设置、工艺及局部姿态缓存每源几何，邻网规则仍以更新后的完整布局验证。拖动中的分叉只展开当前源路径，快速显示不运行完整原生候选搜索，后台检查完成前保持constraints_complete=False。原生检查缓存按实际几何和规则，邻网变动仍须验证。

工程JSON为schema5。`.dsresults`保存已完成的评估、当前帧、宽度统计、重构证据和路径数据，绑定源码版本、几何、参数并校验摘要。使用有限类型的JSON+Zlib/WKB/数组编码，不执行用户pickle。未完成任务不写成完成快照。过期/损坏缓存只丢弃结果，工程设置仍恢复；没有有效历史时先评估再分析。

## 导出与开发要点

普通DXF冻结当前完成姿态；动静区DXF固定使用原始名义零位及名义分区，只输出两种边框与固定RDL的ABC。三层共享相同坐标源，开放轮廓联合闭合，禁止内部切面、HATCH、重复或正长度重叠。实际临时文件回读通过后原子替换，失败保留旧目标。

线程仅处理数据快照；Qt与Matplotlib在主线程。拖动后台任务在请求时捕获对应求解器；不得等防抖计时器触发后再读取可能已被前台清空的engine。共享几何键必须含实际旋转中心。原生KLayout访问有锁，增加线程不一定提速，须实测相同输入/规则/缓存状态。大量只读GEOS与数组批次可以并行；候选和缓存有界，循环检查取消。保留离线模块/桌面后端与CPU回退。

## 后续计划和验收优先级

每轮先复现用户图纸对应工况，再做合成独立期望。重点：多端合并域的实际宽度与局部可逆性、跨叉反向拆分、真实端孔与A工程指定坐标、增删/保存的稳定身份、仅单源重算的计数、结果跨程序恢复、全部离散因子与完整帧一致、零位DXF字节及拓扑。性能记录冷规划、局部编辑、缓存命中、显示耗时分别列出。

尚未完成的内容必须写KNOWN_ISSUES和轮次记录。正式发布仍按Agent.md的Windows、源码包回下载、GitHub合并规则执行；测试代码和报告不构成工厂签认。


## V43 IPC / 大批量实现入口

- `measured_ui.py`：输出栏目、自动保存/评估、GUI锁定、任务注册表、命令调度、批次资源门控；Qt对象只由主线程操作。
- `local_ipc.py` / `ipc_client.py`：HMAC本地UnixSocket/WindowsPipe、JSON bytes（不recv pickle）、生命周期文件锁、CLI启动器及任务等待。入口含密钥，默认用户私有目录，不能入Git。
- `measured_core.py`：冻结工程Session，统一实际中心，线程私有求解器/缓存及共用名义计划。等价中心必须归一为同一身份，防止每条命令重新规划。快批明确diagnostics=False，不等于全DRC签核。
- `oas_stream.py`：显式OAS基本记录、通用点列、CBLOCK、CRC；量化后平移等价图形复用Cell。SQLite仅存去重索引/实例位置，几何按块直接写同一临时OAS，end补TopCell与尾记录，验证后原子替换。不能为方便把几万Cell重新全部读入内存。
- `area_filter.py`：显示索引↔未过滤合并源索引，统一实体/孔洞阈值；Q节点回写工程必须先canonical_project，不能把过滤后的编号错绑定到原图。
- 接口合同/限额/错误见[MEASURED_OUTPUT_API](MEASURED_OUTPUT_API.md)，独立[GD55脚本](../tools/verify_measured_gd55.py)只用标准库调用。

资源上限是增量工作预算，原生调用只能安全点取消，不能宣传操作系统硬隔离；记录队列Busy、错误项不替换旧文件、整个批次失败后重开。批次期间其他工程变更禁止。后续优先用相同图纸/状态测量准备与稳态吞吐，区分首次规划、每姿态几何、流式I/O，而非只报总时长。既有分析/普通DXF不因为IPC开关而改变算法。

## V44 RealDataAnalyse

独立客户端目录：excel.py只读解析/磁盘join及可替换列映射；storage.py SQLite WAL/FULL日志与OS文件锁；transport.py有界认证/响应截止、同目录进程启动与PID出生时间检查；runner.py批次回执、冻结身份、整批重放与write-ahead发布；gui.py后台QThread和200行分页；main.py图形/自动化入口。新本地接口字段为info.session_identity、instance_id、batch_owner及batch.begin.client_id，协议1向后兼容，可靠客户端要求这些字段。Busy不缓存为最终回执，同request_id通信重试不重复接受命令。

发布白名单增RealDataAnalyse全部运行必需文件与Jobs/GD55六文件；GD55公开授权来自本轮用户后续明确指令，其余私人数据仍禁止发布。新回归test_v44/gui_v44/gui_v44_process；Windows验收实际两个相邻EXE及从非安装cwd启动。未提交批次必须整批重放，单项computed不是成品。日志不写IPC密钥。真实Excel模板尚待提供；在excel适配层扩展而不是更改姿态/几何内核。

## V44重构内核增量

`topology_routing.py`检查最终整面铜而非单条候选：同网组件/孔洞数量、真实端孔正面积相接、异网铜/过孔隔离。先共享窄口切分，再有界收窄/自由空间绕接；真实尺寸违规仍由原生DRC标记。无法满足硬约束时GUI/IPC拒绝提交并保留上一状态，不能把断线/警示画成铜。`continuous_warp.py`逐步推移复杂多端域，防止一步位移场折叠。

`branch_regions.terminal_tree`为新自动方案构造连通端点树；有旧手工位置的图形保留完整旧路径身份。动区标识区分同一源的多个域；共享端柄同步对应分支，弧长沿本地指针步进，不跳到相邻回头段。整孔盘圆点按真实端孔中心而非中间权重变换。

`reconstruction_joint.py`在跨网络补偿会耦合几何时，复用局部原始形状并联合执行与画布相同的补偿/检查。扫描和良率复用完成判定；不能继续乘单网边际概率。无耦合时保留原有因子分解。可用`tools/verify_reconstruction_continuity.py <工程> --all --output <JSON>`验证完整离散目录，`tools/verify_nu_handles.py <nu17123工程>`复验真实鼠标。外部图纸不会写入报告。

V45用户清单单独排期：必须先完成V44双方联合发布，再做新的UI精简/统一当前态DRC/按需结果分析等改动。不得提前更改V44缺省值。

## V48 RealDataAnalyse后续入口

计划持久化/捕获/执行见plans.py，独立面板plan_ui.py；实例匹配instances.py，传输身份/追踪transport.py，脱敏有界会话conversation.py，可调表格widgets.py。Runner将批次定向Cache，发布前CRC/SHA与旧目标保护，Output唯一最终文件。发布后恢复使用write-ahead记录；旧结果归档映射同步计划行。新单元测试test_realdata_plans、Qt测试gui_realdata_plans与真实进程gui_realdata_plans_process进入聚合/Windows双EXE验收。
V47最终资产和测试证据见V47_RELEASE_VERIFICATION与V47_VALIDATION。正式tag e51df6e不可改写；PR34后续分页草稿未纳入，保留来源历史。下一轮从最新main同步，勿因Release补文档而重打同名tag。

V48已发布：PR39、标签8536446；实际资产验证见V48_RELEASE_VERIFICATION.json。PR34分页草稿完整历史已纳入V48，不再单独发布该草稿。V48后续改动须从最新main递增版本，不能重写V48标签；运行代码/测试/构建与已通过双Windows的7be169d一致。