# Die Shift Analyze V42

V42变更见[工作记录](docs/V42_WORK.md)，实际测试与限制见[验收记录](docs/V42_VALIDATION.md)。源码交付见[V42 Release](https://github.com/shiyuliuth-del/PLP_Die_Shift_Analyzer/releases/tag/V42)。

软件分析Die-first封装的DIE平移与旋转，提供三方案初筛、实体覆盖与极端足迹、通道宽度风险、重构动区编辑、规则/联合模型良率、工程结果保存及DXF输出。长度均为μm，GDS只读，工程节点可编辑。

- [用户操作](docs/V42_使用说明.md)
- [AI交接：主要功能、代码地图、算法和后续重点](docs/AI_HANDOFF.md)
- [现行设计合同](docs/DESIGN_REQUIREMENTS.md)
- [已知边界](docs/KNOWN_ISSUES.md)
- [每轮记录](docs/Agents.md)

开发者先读[AGENTS.md](AGENTS.md)与[Agent.md](Agent.md)。唯一长期主线为main；短期任务经PR集成，不覆盖其他Agent工作，不重写历史tag。旧版重复说明从主线清理，可在相应tag中查阅。

Windows完整解压源码，RUN.bat启动，BUILD.bat在目标电脑用已有Python/PyInstaller构建单文件EXE；Linux使用`python3 main.py`。依赖不锁定版本，运行不联网补装。缺少Python KLayout模块可调用已安装桌面KLayout，不捆绑整套软件。GPU失败回退CPU。

正式Release仅源码ZIP与SHA-256，无EXE、图片、用户图纸和缓存。Jobs/logs缺失会在运行目录创建。工程JSON引用的.dsresults是用户结果侧车，需随工程一起保留；不进入源码包。

验证入口：`python3 tools/check_contribution.py`、`python3 tools/package_release.py verify-source`。源码验收、Windows托管验证与工厂/GPU设备验收分别记录；完整有限网格不代表连续空间穷尽，模型良率不代表产线实测。
