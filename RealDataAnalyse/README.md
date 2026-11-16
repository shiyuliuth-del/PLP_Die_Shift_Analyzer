# RealDataAnalyse V44

与 Die Shift Analyze 同包发布的独立 Python/Qt 实测客户端。Windows 双击本文件夹 `RUN.bat`。根目录 `BUILD.bat` 一次构建 `dist/Die_Shift_Analyze.exe` 和 `dist/RealDataAnalyse.exe`；本文件夹 `BUILD.bat` 可仅构建客户端。两个 EXE 保持同目录。软件不会搜索系统其他目录的 Die Shift Analyze。源码运行用父目录的 `main.py`；冻结客户端只找自身目录中的 `Die_Shift_Analyze.exe`。Python解释器沿用已有环境，可设置 DIE_SHIFT_PYTHON；不联网自动安装依赖。

## GD55 快速测试

完整案例在 `../cases/GD55`，包含图纸、相对路径工程和3份合成偏移Excel。先按案例README打开工程，再开启“输出→实测输出”。运行本客户端，连接该窗口显示的IPC入口，核对工程与DIE数量。

1. 选择每颗DIE的Excel。文件顺序不限，靠 `die` 和 `package_id` 匹配，不能按行号拼接。
2. 设置DIE数量、并发线程、内存预算、重试次数、任务超时。先连接查询可自动读取DIE数量。
3. 点击“校验并创建任务”，保存新的 `.sqlite` 任务记录。全量校验通过才生成可执行任务；失败不能丢行继续。
4. 选择最终OAS，点击“运行 / 继续补算”。只有全部封装成功、服务结束批次、数量/路径/CRC核对通过才替换最终文件。
5. 意外退出后重新打开客户端，选原任务数据库、原IPC入口、原工程和原输出，继续。可调整并发/内存/超时/重试上限；工程身份变化则必须新建任务。

## Excel 约定与后续适配

当前支持 `.xlsx`，每个Excel一颗DIE，默认工作表 `Measurements`，第1行为表头。旧 `.xls` 需另存为 `.xlsx`。界面JSON可调整工作表、表头行及列名，单位固定为μm与度。后续真实模板的解读集中在 `excel.py`。

| 列 | 含义 |
|---|---|
| package_id | 封装文本ID，保留前导零；多批号请使用批号与封装号组合键 |
| die | 工程DIE编号，1起，每个文件固定一颗 |
| dx_um / dy_um | 相对封装的DIE平移，μm |
| angle_deg | 旋转角，度，±360°内 |
| package_x_um / package_y_um | OAS中名义封装边框左下角的绝对摆放坐标，μm |
| center_x_um / center_y_um | 可选绝对旋转中心；两项都空沿用工程，两项都填才覆盖 |

必填值不能空白、非数值或无限大；公式不作为可信实测数据，需先粘贴值。重复ID、同DIE跨文件重复、缺DIE、同封装摆放坐标冲突均拒绝。长度/中心上限±1e9μm。相同摆放坐标可以表示有意叠放，软件不擅自改坐标。工作簿输入前后SHA-256一致才接受。任务保存输入哈希、原文件行号和规范化数据，继续运行以已冻结任务数据为准，修改Excel后须新建任务。

## 并发、恢复与日志

Excel只读流式解析，最多16个文件并行，各自临时SQLite后磁盘关联。批量最多64个客户端线程，服务实际线程仍受CPU、内存与用户限额约束。仅保留与并发数等量的Future；界面每页200条。更多线程不保证原生几何更快。

通信错误使用相同请求ID有界重试，Busy有退避。任务数据库采用WAL和FULL同步；OS文件锁阻止同任务/同输出的双重运行。批次带客户端归属，恢复不会取消别人的批次。冻结身份包含版本、源图形与Q节点、TopCell、用途/顺序/运动归属、工艺和设置。

服务确实退出时尝试启动同目录程序；PID与创建时间辨认，不能只看PID是否存在。服务仍活着但超时，不强杀、不重复启动；记录后等待人工处理。客户端停止会安全取消自己的批次，之后可继续。用户在服务端取消不会被当作成功输出。

**未提交批次完整重放**：批次几何保存在服务的临时文件中，崩溃后已算封装也不能直接从计数恢复。客户端会重算整批，避免漏算；已验证并发布的最终OAS不再重算。重命名前先持久化哈希与完成回执，可恢复“文件已替换、数据库状态未更新”的断点。目标被外部改变时拒绝覆盖。

确定性输入/几何错误立即停止并保留失败项，修正工程后新建任务。MemoryError重试时降低客户端并发；若服务单任务预算仍不足，需提高预算后继续。超时、磁盘损坏、断电和原生库问题不能保证自动解决；只有实际完成的文件标记 committed，computed 仅表示计算回执完成。

实时轮转日志为程序旁 `logs/RealDataAnalyse.log`（8MiB×9），启动子进程输出为 `logs/RealDataAnalyse-child.log`。SQLite events保留任务事件，packages保留逐项状态/回执。IPC密钥不进入日志/数据库；不要分享IPC入口JSON。崩溃可能遗留服务 `.dsa-batch-*` 或客户端 `RDA_<任务>_<尝试>.oas`，仅在相关服务完全停止、已确认任务状态后清理。

## 全部命令手动调试

第二页提供15个命令：ping、info、app.start、project.open、project.close、session.prepare、output.configure、preview.submit、export.gds、batch.begin、batch.submit、batch.end、task.get、compute.cancel、app.shutdown。JSON参数可编辑，批次ID/任务ID从最近回执自动填入模板。可选等待异步完成；取消等待不等于取消服务任务，需发送compute.cancel。app.start使用界面工程和入口，并固定查找同目录软件。

正常批量不启用allow_failed。手动调试中可以显式发诊断命令，但不能将诊断文件称为合格产品。批量OAS跳过完整DRC/概率/良率；几何与连接成功不等于工艺签核。

无界面重复执行可用 `python RealDataAnalyse/main.py --run config.json --result result.json`（EXE接受相同参数）。config字段为database、endpoint、project、output的绝对路径，以及workers、memory_mb、retries、timeout；任务先在GUI导入。此入口也用于Windows两EXE实际验收。
