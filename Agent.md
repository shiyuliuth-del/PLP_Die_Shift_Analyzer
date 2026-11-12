# 多 Agent 协作与 GitHub 提交规范

适用范围：本仓库全部源码、文档、示例和发布物。维护日期：2026-11-12。本文以 **V38（共用动区与分区DXF）**编制；当前软件版本以`__init__.py`和README为准。原附件自身仍标记为V21，不能把它与此前已发布的V21标签混为一谈。

## 1. 唯一可信主线与需求优先级

- 规范远端：`https://github.com/shiyuliuth-del/PLP_Die_Shift_Analyzer.git`；唯一长期主线：`main`。
- 所有新任务以最新 `origin/main` 为起点。不得从旧源码ZIP、旧桌面副本、历史图文报告或未合并分支建立另一条“最新版”主线。
- 历史 tag 保留用于复现；短期任务分支不是第二主线。禁止新增长期并行的 `master` / `develop` / `latest` 版本体系。
- 最新用户明确指令 > 当前设计合同 > 历史说明。代码是当前行为的证据，不是“代码现有问题必须永远保留”的理由。未授权更改含义时，应记录分歧；接到修复任务后可按证据修正并同步文档。
- 必读：[设计要求](docs/DESIGN_REQUIREMENTS.md)、[基线交接](docs/BASELINE_1102.md)、[已知问题](docs/KNOWN_ISSUES.md)。本轮接入专门记录了与本对话旧要求不同的1102行为。

每轮从本地维护的 [Agents.md 轮次记录](docs/Agents.md) 开始，完成验证、记录新工程字段和兼容措施后通过 GitHub PR 集成。

## 2. 开工与并行协作

1. 读取 `AGENTS.md` 和本文件，确认任务范围与用户已给出的授权。
2. 检查 `git status --short --branch`、`git remote -v`，执行 `git fetch origin --prune --tags`，确认主线最新提交。
3. 从 `origin/main` 创建短期分支。Codex 使用 `codex/<topic>`；其他 Agent 可使用 `agent/<agent>/<topic>`。一个分支只处理一个明确任务。
4. 每个并行 Agent 使用独立工作树/目录，不共享可变工作目录。不得清理、覆盖或提交其他 Agent 的未完成修改。
5. 在 PR 描述中登记任务、影响模块和测试；发生模块重叠时由集成者协调。不得为了解决冲突把自己的文件整份覆盖主线。
6. 提交前再次同步 `origin/main`。冲突必须逐处检查算法含义、UI契约、工程兼容及测试，不只检查语法。

本仓库不要求为每个可逆实现步骤再次确认。按用户已经授权的范围完成工作；仅在真正缺少必要信息或需要新的不可逆授权时询问。

## 3. 设计与实现约束

- 精确保持用户指定的中文标签、控件顺序、默认值、单位和快捷键。按文本/键绑定下拉选项，避免硬编码旧索引。
- 源 GDS 保持只读。Q 的名义节点修改只属于当前工程，改变后的几何须进入连接、评估、缓存及 CLI 的共同入口。数值几何、界面叠加、报告必须引用同一姿态和同一几何版本；推定归属、局部半径、近似模型和未完成结果必须显式说明。
- 不能把 已经退役的 `预演分析` 的验收规则机械套用到 `修复补块` / `重构预演`；后两者允许画出问题假设，必须按各自口径提示，而非伪装成已通过。
- 修改计算含义时补全：公式、符号/单位、参数来源、输入→计算→输出、边界与测试。引用原始输入与实际计算记录，不能引用一份陈旧的成功日志作为本次证据。
- 新参数须同时覆盖校验、保存/恢复、缓存键、界面即时生效及报告。可从GDS推导的内容不再要求用户重复输入。
- GUI线程负责Qt/Matplotlib对象；工作线程只使用快照和数值/几何结果。引擎独占租用，结果用代次/请求票据发布，过期任务不能覆盖新选择。
- 保留CPU回退。检测到显卡不等于可用加速，不允许运行时联网补装依赖。Windows离线桌面KLayout后端与源码/PyInstaller入口均须保留。
- 性能优化必须记录图纸规模、姿态、硬件、冷计算、缓存命中和绘制耗时，不能把取缓存时间称为端到端帧率。

## 4. 最低验证要求

在已有依赖的环境中运行；Linux用 `python3`，Windows用已选定解释器（通常 `python`）。

```bash
python3 tools/check_contribution.py
python3 -m unittest discover -s tests -p 'test_*.py' -v
QT_QPA_PLATFORM=offscreen python3 tests/gui_v21.py
QT_QPA_PLATFORM=offscreen python3 tests/gui_repair.py
```

按改动范围选择额外检查：

| 改动 | 必查内容 |
|---|---|
| 归属/层序/旋转 | `test_v060.py`、`test_v21.py`、`test_v26.py`、`gui_v26.py`、极端状态与M键；多芯片、外部扇出、推定归属 |
| 固定 RDL 通道 | `test_width_channels.py`、示例 O、P、Q 属性与 gui_v25.py 的真实界面回归。验证三档、原宽、名义根数、固定障碍、工程恢复和动画 |
| 直线补块 | `test_repair_patch.py`、`gui_repair.py`；仍接触时不补、新旧DRC分开、直径与定位 |
| 切分重构 | `test_reconstruction.py`、`gui_v21.py`；斜线、串联落盘、共享铜、旋转、零裂隙 |
| 几何型变/DRC | V12–V20相关核心测试；失败保留源铜、原材料随动、固定端、连通、最终全局复核 |
| 概率/安全盒/良率 | 解析或独立积分对照；0/有限k/∞、最大幅度、共同DIE状态与误差范围 |
| GUI/缓存/调度 | `gui_smoke.py`、`gui_v14.py`、`gui_v20.py`、`gui_scheduler_v20.py`，再测当前界面、低容量缓存正反向复用、设置折叠、标签避让与极端表缓存色 |
| 依赖/打包/后端 | `main.py --smoke-test`、安装的KLayout桌面后端、Windows 双击 BUILD.bat、源码 RUN.bat、在新目录验证目标机生成的 EXE |

使用 `python3 tools/package_release.py verify-source` 在干净源码目录运行完整回归。Windows 端单独执行 `tools/windows_acceptance.py`，核对真实结论后才能发布。实际GUI、KLayout、Windows、GPU或真实GD55未验证时，必须写明未验证/跳过原因。不得把“没有数据”“没有标记”“样本未命中”写成通过。

[已知问题](docs/KNOWN_ISSUES.md)中的复现不因现有测试全绿而关闭；修复时新增能够独立验证期望结果的回归，不能写一个断言来永久固化错误结果。

## 5. Commit 与 PR 要求

- 使用明确的提交主题，例如 `Classify fixed-RDL channels by retained linewidth`。一次提交只包含相干改动；来源导入、行为修复、规范整理可分开，方便审查与回退。
- 用明确路径 `git add -- <files>`；不使用 `git add .` 混入别人的产物。提交前检查 staged diff 和文件清单。
- 源码/文档检查：`git -c core.whitespace=cr-at-eol diff --check -- . ':(exclude)figures/*.svg'`。SVG由绘图工具生成，允许其路径数据空白，不借此放过源码错误。
- 每个完成任务推送自己的分支并创建指向 `main` 的PR，使用仓库模板。PR必须写：解决的问题、最终行为、变更文件、兼容/模型边界、实际测试命令与结果、未验证项、是否改变设计合同/缓存/工程格式。
- 禁止在PR中上传访问令牌、私钥、用户目录运行日志、私有图纸、字节码、虚拟环境、临时数据库或安装包。公开仓库尤其要检查这些内容。
- 不自动@其他人员或请求未经授权的外部通知。协作身份来自任务/PR，不捏造已获审核。

## 6. 合并与主线收敛

- 合并前确认PR最新头提交就是已测试的提交，检查主线是否前进、冲突、远端检查与讨论。
- 用户已经明确授权合并时，完成检查后可直接合并，不重复索要同一授权。未授权合并的任务交付可审查PR。
- 优先使用保留来源历史的普通merge；如用squash/rebase，应保留来源、测试和变更含义。**不强推main，不改已有tag，不删除其他Agent未合并分支。**
- 自己的PR合并后删除其远端任务分支，使`main`保持唯一长期开发入口。其他在途任务分支允许暂时存在，不能为“只剩一条分支”而丢弃未合并工作。
- 合并后拉取 `main`，确认提交/版本和关键文件哈希；报告PR链接、合并SHA、测试与遗留问题。
- 本轮提供PR模板和本地检查入口；没有把文档规范冒称成已启用的GitHub Actions或分支保护。仓库级审核人数、自动CI和保护策略应由维护者明确配置。

## 7. 版本、Windows 软件包与发布

- 1102 接入标签为 V22，本轮为 V38，后续每轮依次递增。由集成者统一分配发布号；并行Agent不要各自占用同一个号。
- 更新 `die_shift_studio/__init__.py`、README、当前使用说明、`AGENTS.md`入口链接及发布记录；旧tag及历史证据不改写。
- 正式 Release ZIP 使用 `python3 tools/package_release.py make-source` 生成，包含应用源码、RUN.bat、BUILD.bat、依赖清单、GPU 核函数的 CUDA C 和 PTX 文本及 PyInstaller 规范。图标在目标电脑打包时从源码生成。校验值独立发布。完整 Git 检出保留开发资料和示例，自动源码归档与正式 ZIP 的源文件逐字节相同。
- Windows 验收从正式源码 ZIP 解压后运行 BUILD.bat，验证窗口、工程保留、目录重建和源码启动，临时 EXE 不上传。下载后的源码资产核对文件清单、CRC、SHA-256 和 7-Zip。
- 发布经任务授权后上传版本化ZIP与SHA-256文件。重新下载远端asset核对字节/哈希，检查GitHub自动zip/tar内容；不能只验证本地ZIP。
- Windows 构建须使用 Windows 原生执行环境。源码运行阶段不联网安装，不锁定 Python 或包版本；在 Linux 运行源码回归单独记录。

## 8. 每个任务的交接

最终说明至少包括：改了什么、为什么、从哪条主线提交开始、提交/PR/合并SHA、具体测试、交付路径、已知问题。改变当前设计时同步[设计要求](docs/DESIGN_REQUIREMENTS.md)与使用说明；审阅发现但本任务未修复的缺陷写入[已知问题](docs/KNOWN_ISSUES.md)，不要静默藏进日志。
