# Die Shift Analyze V40

V40 修复重构极端筛选、动区违规配色和连接样式切换任务滞留，增加“边界角度”和按源图形忽略重构。动区详情共用宽度分析的画布标签；后台复用路径，动静区 DXF 复用完成帧并逐层并行、流式输出。

当前行为、公式与限制见 [V40 使用说明](docs/V40_使用说明.md)，实际验收见 [V40 验证记录](docs/V40_VALIDATION.md)。基线为 V39 主线 `04093da99640c5513b2bd15db57abc7b614853de`。

## 开发入口

唯一长期主线为 `main`，规范仓库为 [shiyuliuth-del/PLP_Die_Shift_Analyzer](https://github.com/shiyuliuth-del/PLP_Die_Shift_Analyzer)（末尾有 r）。

参与开发前阅读：[AGENTS.md](AGENTS.md) → [Agent.md](Agent.md) → [设计要求](docs/DESIGN_REQUIREMENTS.md) → [1102 基线](docs/BASELINE_1102.md) → [已知问题](docs/KNOWN_ISSUES.md) → [轮次记录](docs/Agents.md)。每轮基于最新 main，通过 PR 集成；不删除其他 Agent 的在途分支。

## 启动与交付

从 [Release](https://github.com/shiyuliuth-del/PLP_Die_Shift_Analyzer/releases) 下载源码 ZIP，完整解压。Windows 双击 `RUN.bat` 启动源码，双击 `BUILD.bat` 在目标电脑生成单文件 EXE。Linux 使用 `python3 main.py`。

打包使用目标电脑已有的 Python、PyInstaller 和运行依赖，不锁定包版本，不在运行时联网补装依赖。可用 `DIE_SHIFT_PYTHON` 选择解释器。没有 Python KLayout 模块时，程序可发现已安装桌面 KLayout，使用离线命令后端；不会捆绑整个 KLayout 软件。GPU 走系统 NVIDIA 驱动，失败可回退 CPU。

正式发布仅提供源码 ZIP 与 SHA-256，不含图片、用户 GDS、缓存或 EXE。程序图标在目标电脑构建时生成；Jobs/logs 等目录在运行目录缺失时自动建立。完整测试、开发文档和示例保留在 Git 检出。

## 常用操作

1. 在“偏移”打开 GDS，手动指定封装/芯片边框、运动/固定 RDL 与运动/固定过孔。新图纸默认全部不参与。显隐 checkbox 只控制画面，不改变计算；拖动行调整层序。
2. 工艺分别设置运动/固定线宽、线距和包围量。偏移单边幅度是最大随机偏差，支持逐芯片参数与实际旋转中心；良率级别为 0–12/∞。非零有限 k 用母尺度 B/k 并在输入范围截断；0 沿用最近有限尺度，∞ 接触显示按当前几何二值判断。
3. 点击“方案评估”，再选择极端行、数值输入或 M 键手动偏移。首行/“复原”回零并保留表格滚动位置。每轴 0/最小/最大组成离散目录，零幅度轴锁零；默认重点组合和过滤无问题情况。
4. “极端足迹”合并离散孔实体与覆盖概率，∞ 只画当前实体。Space Map 表示位置相关线距需求。接触标记及低概率标记默认隐藏；两种过孔虚线范围可分别设置。
5. 宽度分析按名义线路需求检查相邻刚性孔盘/固定障碍/封装和禁布区通道。原宽可过、需收窄、最小线宽仍不足分三档；零位汇总全部离散工况。右键可忽略线路；“全修正良率”和“不修正良率”按联合宽度事件计算，有限网格不是连续空间精确穷尽。
6. 重构预演把固定 RDL 分为 A 动区、B 固定端静区、C 运动端平移区，共用原图端点。蓝黄柄独立拖动，两端整孔盘模式可逆；连接样式为直线/一阶/二阶/圆弧，边界角度为垂直/重构区优先/两侧平均。当前/全极端风险按红黄绿灰显示，规则位置另标记；左键选中显示画布详情，右键可忽略整个源图形的重构。
7. 普通 DXF 输出保存当前完成几何；动静区输出只将固定 RDL 分为 A/B/C 三层的真实开放轮廓。默认 Flatten 所选 Top Cell 并按层 Merge，不恢复圆弧。分区交接共用相同坐标，无人工内部截面、无 HATCH，失败保留旧文件。

图片滚轮缩放、中键平移；普通右键拖框缩放、单击图形菜单。**F** 恢复视图，**M** 选择 DIE 后右键拖移、滚轮旋转、Ctrl+滚轮缩放，**ESC** 退出/取消选择。左键点选或框选，Shift 追加、Ctrl 扣除；边框不参与选择。**Q** 查看/修改源图形节点，修改保存在工程而不回写 GDS。表格/信息框允许滚动，数值和下拉控件禁止滚轮改值。

工程下拉自动读取 Jobs；已有工程保存覆盖，新工程保存时输入名称。schema5 保持旧字段兼容，新字段缺省恢复。缓存按条件复用，零位固定保留；重评估清当前工程，关闭软件清会话缓存。设置中可清当前或全部工程。

旧“预演分析”已在 V38 退役，不要与当前“重构预演”或“修复补块”混淆。研究中的三方案 U 指标是空间初筛，不代表完整路由、工艺放行或实测良率；离散极端通过也不能证明所有连续偏移通过。

## 验证

```bash
python3 tools/check_contribution.py
python3 tools/package_release.py verify-source
PYTHONPATH=tests:. python3 -m unittest test_v40
QT_QPA_PLATFORM=offscreen python3 tests/gui_v40.py
python3 tools/benchmark_v40.py --gds /path/to/GD55_9-21.gds --dxf /tmp/GD55_ABC.dxf
```

Linux、托管 Windows 和物理 GPU 的验证分别记录。有关概率精度、复杂铜区无可信分区和冷计算耗时等限制见 [已知问题](docs/KNOWN_ISSUES.md)。

历史：[V39 连接样式](docs/V39_使用说明.md) · [V38 共用动区](docs/V38_使用说明.md) · [V36 DXF/概率](docs/V36_使用说明.md) · [研究报告](https://github.com/shiyuliuth-del/PLP_Die_Shift_Analyzer/blob/main/DIE_SHIFT评估报告.md)
