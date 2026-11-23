# V64 运动孔盘圆点许可

`ViewSettings.reconstruction_allow_moving_pad` 控制运动端整孔盘模式，旧工程缺项取关闭。名义路径的 `pad_modes` 同时检查该设置和路径端点的过孔归属。`RoutePlan.moving_line_partition` 将零弧长切口移至孔盘外最近可用截面。二维铜面、手动框选和多分支共享同一限制。预演缓存、逐源复用和动静区 DXF 按新设置区分。改变许可会刷新当前姿态，同时保留已暂存的其他重构编辑。

`apd_export` 先按固定 RDL 和线距分档合并有效区域，输出与 23.100 样例一致的 Region 线距覆盖及创建同名几何的 SKILL。导出不复制样例的 Net、Stackup 或全局开关。

`area_filter` 在源图形进入计算前应用工程删除记录，并重新映射活动图形索引。`edit_history` 保存待恢复的源绑定状态，工艺参数不进入历史。几何删除和撤销走同一个异步几何提交流程，重构暂存标记与已删除的实际铜分开。

# V63 实测阈值与输入发现

`measurements.package_rows` 在分组完整后记录所有 DIE 源偏移绝对值的逐轴最大值，避免分组拟合余差跨过阈值。`Runner` 在批次提交前比较阈值，发送 `use_nominal`，同时核对该回执及最终数量。共享 OAS Cell 在第一个直出封装提交时冻结，后续仅写参考。正常任务保留实际铜 DRC、局部失败裁剪和所有诊断层。既有发布和重试逻辑仍保护旧 OAS。

`projects.load_folder` 查找 Excel 的 DIE 目录所在层级。项目保存仅排除用户明确从表格删除的文件，不将尚未扫描的新文件误删。工作区及 Windows 构建沿用 V62。详见 [V63 使用说明](V63_使用说明.md) 和 [IPC约定](MEASURED_OUTPUT_API.md)。

生产导入的 `ignore_measurement_failure` 默认关闭。仅在启用时按机台失败行填报的偏移和角度恢复姿态，优先保留同一芯片的有效成功行。任务库记录选项和实际采用的失败行，普通继续任务比对选项，计划任务从冻结设置导入。

# V63 工艺参数、实测模式与重构预演

三项几何阈值由 `MainWindow.build_settings` 挂载到「工艺 → 其他」。工程继续用原设置键保存。「设置 → DRC」仍由 `drc_policy.build` 生成。

`MeasuredOutput.submit` 及进程池调用 `Session.run` 时不传进度回调。底层按需传递回调，避免计算百分比、拼接进度字符串和跨线程发送进度信号。内存监控通过独立检查点执行，取消和 DRC 目录仍通过 IPC 返回。

名义动区在同一重构计划内按条目复用。切口分区按弧长与角度精确缓存，边缘切向量复用原图形的线段数组。计划或设置变化时重新准备，实际铜仍按当前姿态做规则与连通检查。

动静区 DXF 恢复 V61 分段规则。ABC 块中的实体都是开放多段线，整圈属于同层时由两段拼出原轮廓。重复轮廓仍去重，边框作为独立闭合实体输出。

# V62 启动入口、工程目录和 DXF 轮廓拼接

根目录 `BUILD.bat` 一次构建两个 EXE，复制两份独立启动脚本到 `dist`。启动脚本检查同目录和 `dist` 的可执行文件，再检查源码和解释器。项目目录继续通过 `jobs.provision_project` 管理，首次保存工程并预置实测目录。Windows 验收用源码、只有 EXE 的目录和含 `dist` 的源码目录分别调用两个脚本。

`drc_policy.build` 返回 DRC 栏目的表单，接触标记显示参数复用现有设置键。`shape_menu.py` 依据选中用途提供菜单，运动过孔的增加动区按固定 RDL 接口选择目标。`region_export.py` 在重复区间去除后按源图形及输出层拼接，相接端点按精确坐标连接，分叉保持独立，完整环通过 DXF 闭合位记录。封装参考层为 `PKG_REF`。详见 [V62 使用说明](V62_使用说明.md)。

# V61 动静区 DXF 和 SH22 运动归属

`stack.py` 保留完整电气连接与原有深层根，并优先采用固定 RDL 之前可追溯的运动侧芯片端口。没有更深运动端口时，完整落入单颗 DIE 内的运动过孔提供本地证据。`ContactModel`、预演、重构及实测共用该归属，跨 DIE 网络只根据运动侧的端口归属识别，不把远端固定孔计为运动端。测试覆盖 SH22 三个命中实体和旧例中的跨 DIE 扇出。

`region_export.py` 以原始外轮廓边的参数分割名义区域，并按源图形、输出层组成块。重叠检测及实际文件核验在 `region_contract.py`，内部键到 DXF 后缀只在此映射。参考边框以完整闭合折线输出。共边时复用最先保留的线段，部分重叠仅去掉共有区间，回读核对插入、原点、块内节点、闭合、重合。两套输出必须继续区分普通 DXF 的当前几何与动静区 DXF 的零位轮廓。详见 [V61 使用说明](V61_使用说明.md)。

# V59 双运动端孔、孔盘保留与联合输出

`moving_pair.py` 将单动区的近端位姿转换至对端真实刚体坐标系，求解后的铜、控制柄与失败见证再统一转回全局。`reconstruction.py` 保留真实端孔对应的两个源路由身份。`routing_quality.py` 从源铜提取可识别孔盘，并在已通过连通核验的活跃区域内尝试扩宽和修整内角。`topology_routing.py` 以完整孔盘、完整包围、局部覆盖为候选顺序，并用实际异网铜和端孔复核。概率目录同时记录两端的 DIE 依赖，必要时联合计算最终铜。`gui.py` 只在后台任务仍在运行时显示任务状态。

验证与配合 RealDataAnalyse 输出见 [V59 使用说明](V59_使用说明.md)。

# V57 随动诊断、复杂铜面动区、低概率低亮与层叠选中列

`reconstruction.py` 与 `reconstruction_events.py` 修复整块随动图形失联时的 `missing_region`（改用移动后的 `regions['C']` 而非原位 `source`），并在离散扫描中接入 `ignored_motion.linked_owners`。`ignored_motion.entries` 与 `gui.py` 确保被忽略的图形自动清除全部动区并覆盖旧选框，同时在 `die_internal` 保留所属 DIE 与显式静止/随动优先级。

`branch_regions.py` 吸收被同一 DIE 相邻动区夹住的狭窄无端孔刚体条，并在 `transition_domains` 检出跨成员相交斜切时自动回退垂直切口；`plane_route.live_part` 按跨度与切口高度比例限制最大倾角，避免宽面边界横竖交错及虚假内角 DRC；`reconstruction_route.choose` 在无安全余量段时优先长度达到 `max_travel` 的直段而非亚位移窄条。

`reconstruction_probability.pose_probability` 计算离散极端姿态物理概率质量，`gui.is_low_probability_pose`、`reconstruction_overlay.py`（`LOW_PALETTE`、`low_problems`）与 `drc_overlay.py` 对低于 `2Φ(-k)` 的极端工况进行低亮弱化处理。`ReconstructionOverlay.stage` 记录逐图形累计待算操作（`actions`）并用分类虚线色与画布 `staged_tags` 标注；随动填充提升至 `#c9b3eb`（alpha `0.45`）。`gui.py` 层叠表扩展为 4 列（`显示 / 选中 / 层·类型 / 用途`），`selectable_boxes` 默认仅勾选固定 RDL，由 `selection.py` 过滤并随工程持久化 `selectability`。`reconstruction.frame` 跳过仅含忽略图形硬失败的冗余 `quick` 回退。回归见 `tests/test_v57.py`，操作说明见 [V57 使用说明](V57_使用说明.md)。

# V56 GD55_1119 交互、反馈与性能

`Jobs/GD55_1119`（`GD55_1119.gds`、`GD55_1119.dshift.json`）纳入 `.gitattributes`、`RealDataAnalyse/projects.py` 与 `tools/package_release.py` 随包白名单。

`reconstruction.py` 为双 DIE 无固定过孔线路保留整条源路径，以 `default_fixed_start` 约束自动初始分区、以 `ownership_domain` 归属手动框选，`CutDrag` 按对端 DIE 实际 `A∪C` 支承限位，支持跨 50% 名义缝拖动。`reaches_anchor` 同时覆盖实体固定过孔与归属缝/对端锚点，防止 `inward_routes` 短弦在拐角前截断路径。`reconstruction_zones.expand` 与 `change_zone('delete')` 同步处理 `box_refs` 的删除与重框选。

`RegionSelect` 新增十字落点标记与起点标记，由 `render_cache.SceneRenderer.draw_feedback` 多图元局部增量重绘。`ReconstructionOverlay.stage` 覆盖全部重构字段（含 `fixed_via_follows` 与新增选框），关闭自动计算时通过 `staged_overlay` 与 `defer_reconstruction -> render_scene` 即时高亮标记受影响图形、过孔与选框，`shape_menu` 读取叠加草稿状态。

`ReconstructionOverlay.draw_labels` 先剔除视野外动区并按严重度排序，多动区时不强行叠加；`width_labels.LabelLayout` 对轴对齐矩形采用 NumPy 向量化 AABB 碰撞与面积计算。`RoutePlan.section` 改用向量化射线-边求交（顶点/共线退回 exact shapely），`partition` 先做面积守恒快速验证，`choose` 跳过已等宽段的 25 点重采样，`FixedRules` 按物理层共享 `STRtree` 并按 `active` 邻域过滤异网查询，`native_backend.compute` LRU 增至 1024。回归见 `tests/test_v56.py`，操作说明见 [V56 使用说明](V56_使用说明.md)。

# V55 导入、分叉与固定孔联动

gds_merge.merge_import 分层处理、以有界查询块识别同层实体接触并维护稳定源索引。geometry_errors.sanitize_data 批量校验规则多边形，逐形状保留孔洞修复和错误见证。stack.Connections 复用已编译的图层几何。大批量实测进程上限来自 CPU、可用内存、工程上限与预计任务数量。

branch_regions.assemble 排除固定侧之间的等势区域和没有相对控制截面的残余铜。parallel_zones.coordinate 给相邻等宽长直段协调切口，只更新当前预演中的路线副本。固定侧禁用圆点，旧端点通过 RoutePlan.fixed_side 转成实际截面。

manual_regions 分别持久化自由框及符合角度框。右键删除全部动区移除该源的手动区间、角度和添加记录，并记录对该源的框覆盖例外，避免误删其他图形的框。ignored_motion.linked_fixed_vias 按连接追踪，联动位姿单独加入最终铜替换列表。topology_routing 在补偿前已按最终过孔位置校核端孔。

extreme_validation.catalogue_status 从未分页的计算范围及事件目录折叠零位行状态。开发回归见 tests/test_v55.py，GD55_1119 使用用户原始工程，交接说明见[V55使用说明](V55_使用说明.md)。

# V54 外轮廓与重构分叉

`geometry_rules.corner_angles` 只测已合并铜形的外环，`current_drc` 按物理网络共享角度见证。候选布局的边界横截面仍用于切口样式评分，二者输入不同。

`branch_regions` 将 `reconstruction_ownership` 的归属接缝保留为名义锚点。去掉数值毛边的控制柄只负责显示和命中；连线选用匹配的完整原始截面。串联和分叉动区按各自刚体边界求位移。零位不重新构造连线形状。

`topology_checks.same_connections` 按原材料分量核对孔洞及端孔分组。`topology_routing` 在近邻接缝可以补实际铜并重新验收。最终最近线距按物理层建索引、按网复用，避免每个动区都重建树。

# V53 重构预演交接

`reconstruction_overlay.display_material` 从 `replacements` 获取画布底图，和当前全层 DRC、GDS/OAS 出口使用同一份实际铜。A 区着色裁到实际铜；诊断断口单独绘制。ABC 导出仍使用各区定义。

`CutDrag` 按下时作废旧视图代次，立即更新移动端控制柄并从静态绘图移除该柄。局部计算只使用本源名义线路、端孔和当前姿态。鼠标松开时提交设置，手动模式的最后一帧可异步补画。禁止把拖动草稿用于电连接通过判定或制造输出。`reconstruction_events` / `reconstruction_joint` 在当前目录内聚合 A 区最严重状态，共用同一规则指纹。`FixedRules.check` 按物理层分批查询空间索引，独立求解器复用只读名义规则数据和端孔定义。

V50续检优化：extreme_validation把大重点目录按真实DIE依赖分解，只投影所选行；不构造729份带表格量测的显示帧。检查类别也用于旧机械/接口状态计数，不能仅从新DRC表隐藏。实际值量测仅交互显示，measured_core批量保留全部规则/见证但不量表格尺寸。完整删空铜的boundary可为None，partial_output先判断差集为空。

nul7123复现入口：tools/verify_v50_nul.py与tools/verify_v50_nul_mouse.py，均传入用户外部工程JSON；不附私人图纸。具体本轮证据和首次失败保留于V50_VALIDATION，不为发布而循环扩大检查。

# V50 增量交接

本轮以V50_使用说明为准，旧版同步重点组合已替代。focus_cover.py使用GF(3^m)覆盖完整DIE状态配对；不是仅两条轴的覆盖。branch_regions.transition_domains按全截面分割实体，再依据真实端孔分类；避免包围掩膜亚格点重叠吞并分支。topology_checks.py只并行私有WKB解码后的只读核验，联合改线仍确定性串行；不可再共享原生prepared查询对象并发修改。local_failures.py按真实断口/接触见证定位失败动区，正常动区不连带染色。

layout_export.py负责GUI当前姿态GDS/OAS（参数保存、ERROR、原子提交）；名义ABC保持原出口。drc_policy.py控制分类检查/显示，drc_actual.py只在真实见证附近量值。ruler_snap.py按当前实际铜查询几何，方向锁定优先。Settings.reconstruction_auto是调度开关，不是几何参数；pending时保留上个完成帧并拒绝输出待应用状态。新缓存键含检查类别，显示类别不影响数值身份。

实测ERROR四角已含真实姿态，OAS不能再重复施加DIE旋转；legacy bounds保留。客户端相关代码由协作窗口提交后merge，不能复制工作树。V50验收按用户只做一轮集中检查；失败修复仅重跑相关项。下方历史代码地图供定位，冲突以V50合同为准。

# V49 软件交接

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

注意：A颜色只反映A自身违规，当前DRC表检查全层，两者不相等；V49批量和交互IPC均按指令检查完整实际铜DRC/连接，跳过无关姿态和良率。不要重新加入报告、图形数量扫描或表格专用统计。

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

普通 DXF 冻结当前完成姿态。动静区 DXF 固定使用名义零位，输出完整边框及固定 RDL 的 _A 运动侧、_B 固定侧、_C 动区。三层共享坐标源，开放轮廓按原图形和输出层各自组块。实际临时文件回读通过后原子替换。

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

`topology_routing.py`检查最终整面铜而非单条候选：同网组件/孔洞数量、真实端孔正面积相接、异网铜/过孔隔离。先共享窄口切分，再有界收窄/自由空间绕接；真实尺寸违规仍由原生DRC标记。无法满足硬约束时GUI显示局部断连诊断，严格制造输出拒绝；显式实测部分输出删除失败局部并保留失败记录，不能把断线/警示画成铜。`continuous_warp.py`逐步推移复杂多端域，防止一步位移场折叠。

`branch_regions.terminal_tree`为新自动方案构造连通端点树；有旧手工位置的图形保留完整旧路径身份。动区标识区分同一源的多个域；共享端柄同步对应分支，弧长沿本地指针步进，不跳到相邻回头段。整孔盘圆点按真实端孔中心而非中间权重变换。

`reconstruction_joint.py`在跨网络补偿会耦合几何时，复用局部原始形状并联合执行与画布相同的补偿/检查。扫描和良率复用完成判定；不能继续乘单网边际概率。无耦合时保留原有因子分解。可用`tools/verify_reconstruction_continuity.py <工程> --all --output <JSON>`验证完整离散目录，`tools/verify_nu_handles.py <nu17123工程>`复验真实鼠标。外部图纸不会写入报告。

V45用户清单单独排期：必须先完成V44双方联合发布，再做新的UI精简/统一当前态DRC/按需结果分析等改动。不得提前更改V44缺省值。

## V48 RealDataAnalyse后续入口

计划持久化/捕获/执行见plans.py，独立面板plan_ui.py；实例匹配instances.py，传输身份/追踪transport.py，脱敏有界会话conversation.py，可调表格widgets.py。Runner将批次定向Cache，发布前CRC/SHA与旧目标保护，Output唯一最终文件。发布后恢复使用write-ahead记录；旧结果归档映射同步计划行。新单元测试test_realdata_plans、Qt测试gui_realdata_plans与真实进程gui_realdata_plans_process进入聚合/Windows双EXE验收。
V47最终资产和测试证据见V47_RELEASE_VERIFICATION与V47_VALIDATION。正式tag e51df6e不可改写；PR34后续分页草稿未纳入，保留来源历史。下一轮从最新main同步，勿因Release补文档而重打同名tag。

V48已发布：PR39、标签8536446；实际资产验证见V48_RELEASE_VERIFICATION.json。PR34分页草稿完整历史已纳入V48，不再单独发布该草稿。V48后续改动须从最新main递增版本，不能重写V48标签；运行代码/测试/构建与已通过双Windows的7be169d一致。
V49最终发布范围：b0299ad功能头包含目录并发/完整分页修复；485核心（2外部另验）、38GUI/进程、两后端、17工程/58命令通过。用户要求停止追加复核并直接发布，最终Windows44045057121已请求取消，不冒称完成，不做发布后回下载。后续维护从main/V49继续，双方历史及既有V48标签保持，详细范围见V49_VALIDATION。

V51：[当前使用说明](V51_使用说明.md)。方向键仅在版图画布内平移，K 标尺与中键平移共存。手工框选的直线边界优先，草稿与已核验铜分开；旧工程布尔筛选迁移到三态筛选。

V58实测生产路径在RealDataAnalyse/measurements.py与excel.py。按表读取的理论锚点匹配重复行列号及芯片目录，任务绑定预演工程的名义几何。几何由经认证的同目录预演服务提供，客户端冻结包无需装载KLayout/Scipy。SH22例题通过tools/generate_sh22_measurements.py从授权工程生成；任何真实附件测量都不能入Git。ERROR/ERROR2的严重度来源为实铜短接与重构连接失败，最小外包Polygon由measured_issues记录，oas_stream按当前姿态与层写入。测试覆盖生产规模导入、真实两程序读回和两层诊断。

V60按机台理论中心匹配图纸DIE。在 `RealDataAnalyse/measurements.py` 按重复行列号提取完整候选匹配，多组一致证据确定目录到图纸的物理映射，在局部分组选取可靠记录。目录及旧顺序不控制摆放。警告持久保存于 SQLite events。仅用本机临时副本核对真实机台附件，随包测试用合成数据。用户确认其 KLayout 报错来自环境变量缺失并停止故障复现，本轮不改几何后端。
