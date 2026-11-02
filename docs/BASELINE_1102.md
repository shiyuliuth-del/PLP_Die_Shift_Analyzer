# 1102最新代码接入与协作基线

本文归档 V22。当前宽度分析见[现行设计](DESIGN_REQUIREMENTS.md)。

日期：2026-11-02（Asia/Shanghai）。本记录对应V22；原附件标记V21，保留为独立来源快照。

## 来源与版本

- 用户提供：`1102.zip`，2,931,831 bytes。
- ZIP SHA-256：`c8508ce014b4205f51d8b997b3f9d29cc4b71e1308f663ba769bee3192b66aad`。
- ZIP CRC检查通过；344个条目，展开约7.77 MB。
- 独立来源快照提交：`1cfdf14`（Import the user supplied 1102 source baseline with provenance）。
- [逐文件来源清单](imports/1102_source_manifest.json)：保留234个源码、示例、技术文档/图文件；排除98个运行日志、字节码及过时验证清单。源清单中的图片哈希只记录附件来源，不表示图片进入源码ZIP。
- 输入中的SHA256清单有14个路径与实际文件不符；不将它作为最新代码证书。旧的package_verification也是首次V21的记录，已按历史证据保存，V22包重新生成验证记录。
- 此前GitHub V21提交为`c5c1786`，不覆盖旧tag。V22为本次接入及协作文档版本号。

仓库实际名称为 [PLP_Die_Shift_Analyzer](https://github.com/shiyuliuth-del/PLP_Die_Shift_Analyzer)，不是另建无r的仓库。开始接入时仓库为公开、仅main、无待合并PR；本轮不改变公开/私有属性。

## 与已发布V21的主要差异

| 模块 | 1102现行变化 |
|---|---|
| `stack.py` | 固定过孔中介接口查找、投影下第一处固定铜回退；无根/多根运动铜最近DIE位置推定 |
| `width_analysis.py` / `width_overlay.py` | 删除通道根数/容量；改为落盘air-gap、两种目标模式、R范围、CAD尺寸线和动画风险跟踪 |
| `repair_patch.py` / `repair_overlay.py` | 新增失接触时直线补块、按孔宽默认直径、补后DRC与新增区域归因/定位 |
| `reconstruction.py` / `reconstruction_overlay.py` | 新增串联落盘一刀切分、2.5倍等效直径距离、DIE刚体运动、共享铜裁剪及四端点补缝 |
| `settings.py` / `fast_preview.py` / `gui.py` | 新控件、参数持久化、配置键、四部分帧键、独立repair缓存、实时绘制与报告快照 |
| `report.py` / 使用说明 | 新公式、模式说明、推定归属附录、重构/补块与原评估的边界 |
| 测试 | 新增air-gap、repair、reconstruction核心测试及repair GUI；更新V21 GUI与归属预期 |

变更含义详见[设计要求](DESIGN_REQUIREMENTS.md)。旧对话中的“无根必静止”“宽度按线路根数计算”等已经被1102基线替代，不能作为恢复旧算法的理由。

## 本轮整合做了什么

1. 先提交附件来源快照，保留原实现可审查；运行日志和字节码不进Git。
2. 增加根目录[AGENTS.md](../AGENTS.md)入口与[Agent.md](../Agent.md)，以及设计、已知问题、贡献说明、PR模板与本地检查工具。
3. 更新当前版本和使用说明入口到V22；标明旧研究报告是历史资料，避免被误当现行模型。
4. 扩展源码包白名单，使新Agent指引也进入源码ZIP；加入新repair GUI与贡献检查。没有联网补装本机依赖。
5. **没有改写附件的数值/几何算法**；目前`die_shift_studio`中的非版本模块与附件字节一致。独立复算发现的问题已如实登记到[已知问题](KNOWN_ISSUES.md)，不是本轮已完成的修复。

## 验证证据与限制

原附件独立目录运行核心测试：**234项，成功，1项外部GD55条件测试跳过**（未传测试数据路径）。`tests/gui_v21.py`和`tests/gui_repair.py`均通过，覆盖air-gap模式、重构假设、补块DRC、定位、显隐及缓存。V22发布包会在新的解压目录再跑完整规定检查，记录见[package_verification.json](package_verification.json)。

同时独立复算复现了3处边界问题，并静态记录1处缓存风险；详见[机器记录](../results/1102_edge_audit.json)及[已知问题](KNOWN_ISSUES.md)。不能将原有测试通过写成“所有情况无问题”。

当前验证为Linux既有依赖；没有Windows实机、GPU实测或全工艺DRC签认。附件使用说明内GD55的147段/151处补线等数字是其历史特定状态记录，本轮没有把它当作新跑出的通用结果。

## 主线与后续协作

采用短期任务分支→PR→main的集成流程；本任务用户已明确授权提交并合并。合并后删除本任务远端分支，旧tag保留，main为唯一长期主线。未来其他Agent从main拉取后按AGENTS.md入口阅读，不从旧ZIP继续分叉出另一份“最新版”。

本轮只建立仓库内规范、PR模板和本地校验，没有改变GitHub分支保护或宣称已配置自动CI。当前命令行授权没有workflow范围，因此也没有将启动自动Actions作为提交本次代码的前提。
