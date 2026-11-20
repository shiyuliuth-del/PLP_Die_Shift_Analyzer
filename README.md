# Die Shift Analyze V57

V57 修复 `GD55_1119` 随动残影、复杂铜面双窄条/交叉边界与虚假 DRC 标记，新增重构极端低概率低亮样式、忽略图形自动删动区、关闭自动计算多步操作标签与随动配色优化、层叠表「选中」列及重构预演性能提升。详见 [V57 使用说明](docs/V57_使用说明.md)。

V56 随包新增 `Jobs/GD55_1119` 测试案例，修复双 DIE 跨中点拖动、拐角截断、框选动区删除与重框选问题，新增框选动区落点标记、关闭自动计算时的图形暂存标记、视野外弹窗自动隐藏与重构预演性能优化。详见 [V56 使用说明](docs/V56_使用说明.md)。

V55 加快 GDS/OAS 导入与金属连通识别，修复分叉、平行排线及拐角的动区边界，并增加固定过孔联动设置。详见 [V55 使用说明](docs/V55_使用说明.md)。

V53 修复重构铜形预演接缝、后台计算期间的切口拖动与圆点反馈，并统一动区和极端情况表的严重度。详见 [V53 使用说明](docs/V53_使用说明.md)。

V52 默认采用高风险极端组合，支持实时分页、导入和导出最短边长、框选动区局部扩展以及 DIE 内固定 RDL 随动策略。详见 [V52 使用说明](docs/V52_使用说明.md)。

V51 新增画布方向键平移、单直线边界编辑、三种极端情况筛选与计算资源上限。见[V51 使用说明](docs/V51_使用说明.md)。

V50：芯片对重点组合、分叉边界修正、当前姿态GDS/OAS与旋转ERROR输出、手动重构计算、分类DRC与吸附标尺。详见[使用说明](docs/V50_使用说明.md)。按本轮要求只进行一轮集中检查；失败项仅检查受影响部分。

本轮同时更新主程序测量/尺寸容差/DRC分页、重构失败诊断与规则排序，以及实测完整问题目录、RealDataAnalyse的ERROR矩形输出和GD55扩展案例。实现与操作见[V49说明](docs/V49_使用说明.md)。

保留V46的圆点拖动稳定性、边界显示修复和按源复用重构路径；重点/全部组合统一控制实际评估与良率范围。工程采用独立Jobs目录，实测OAS增加阶段文件；与[RealDataAnalyse](RealDataAnalyse/README.md)及[GD55案例](docs/GD55_MEASURED_CASE.md)同包发布。实际验证与限制见[V49记录](docs/V49_VALIDATION.md)。

软件分析Die-first封装的DIE平移与旋转，提供三方案初筛、实体覆盖与极端足迹、通道宽度风险、重构动区编辑、规则/联合模型良率、工程结果保存、GDS/OAS导入、实测IPC及批量OAS输出。长度均为μm，GDS/OAS只读，工程节点可编辑。

- [实测IPC接口与注意事项](docs/MEASURED_OUTPUT_API.md) · [GD55-1105独立验证脚本](tools/verify_measured_gd55.py)
- [用户操作](docs/V57_使用说明.md)
- [AI交接：主要功能、代码地图、算法和后续重点](docs/AI_HANDOFF.md)
- [现行设计合同](docs/DESIGN_REQUIREMENTS.md)
- [已知边界](docs/KNOWN_ISSUES.md)
- [每轮记录](docs/Agents.md)

开发者先读[AGENTS.md](AGENTS.md)与[Agent.md](Agent.md)。唯一长期主线为main；短期任务经PR集成，不覆盖其他Agent工作，不重写历史tag。旧版重复说明从主线清理，可在相应tag中查阅。

Windows完整解压源码，RUN.bat启动，BUILD.bat在目标电脑用已有Python/PyInstaller构建两个相邻单文件EXE；Linux使用`python3 main.py`。依赖不锁定版本，运行不联网补装。缺少Python KLayout模块可调用已安装桌面KLayout，不捆绑整套软件。GPU失败回退CPU。

正式Release为两个软件的联合源码ZIP与SHA-256，不单独发布RealDataAnalyse。随包 `Jobs/GD55` 与 `Jobs/GD55_1119` 提供公开测试案例。新建工程时自动创建主程序的 `Cache/` 和实测客户端的 `RealDataAnalyse/` 目录树。用户把实测输入放入 `RealDataAnalyse/Excel/` 和 `RealDataAnalyse/Template/`，实测结果保存在 `RealDataAnalyse/Output/`。工程JSON引用的.dsresults侧车需随用户项目保留。旧平铺工程可打开并在保存时迁移。

验证入口：`python3 tools/check_contribution.py`、`python3 tools/package_release.py verify-source`。源码验收、Windows托管验证与工厂/GPU设备验收分别记录；完整有限网格不代表连续空间穷尽，模型良率不代表产线实测。
