# RealDataAnalyse

与 Die Shift Analyze 同包发布的 Python/Qt 实测客户端。Windows 双击本目录 `RUN.bat`，源码使用现有环境的 pythonw，不保留命令行窗口。根目录 `BUILD.bat` 一次构建 `dist/Die_Shift_Analyze.exe` 与 `dist/RealDataAnalyse.exe`；本目录 BUILD 可单独构建客户端。两个 EXE 都是无控制台的 GUI 程序，保持同目录；只在同目录寻找预演软件，不检索系统其他程序目录。构建会将随包 `Jobs/GD55` 复制到 `dist/Jobs/GD55`，已有同名项目不会被覆盖。

## 项目文件夹

点击“选择项目文件夹”，例如根目录 `Jobs/GD55`。文件夹应包含唯一的 Die Shift Analyze 工程JSON及其相对引用的 GDS/OAS。客户端自动发现文件夹中的 `.xlsx`，读取该项目保存的勾选项、列映射与资源设置，并通过本地 IPC 联动打开对应工程；服务未运行时启动同目录程序，已运行且空闲时用 project.open 切换。正在处理其他任务时拒绝切换，不取消别人任务。连接与启动按钮成功为绿色，失败为红色；详细原因在底部进度条及可选信息输出内。

项目内推荐结构：工程JSON及图纸在项目根目录，外部添加的Excel复制到 `Excel/`，任务记录在 `Tasks/`，OAS在 `Output/`，缓存由主程序保存在 `Cache/`，日志在 `logs/`。项目根目录的 `RealDataAnalyse.project.json` 保存相对路径和客户端设置。IPC认证入口是本机服务信息，仍保存在用户私有目录，不随项目复制或公开。

客户端所有工程、Excel、任务记录和输出路径必须位于所选项目文件夹内。添加外部Excel时复制原文件，相同文件名而内容不同会加摘要后缀，不覆盖原数据。删除表格行只移除引用，不删除磁盘文件。工程/输入可整目录复制到另一电脑；旧任务记录绑定原工程与运行路径，迁移后应新建任务，不能将旧回执混入新运行。旧平铺工程可先在新版主程序中另存为项目文件夹；客户端不会静默移动用户旧数据。

## Excel到OAS

1. 选择项目文件夹，等预演软件完成图纸加载、评估及实测准备。GD55随包提供工程、图纸与三份合成Excel，客户端不再提供生成示例按钮。
2. 在Excel表格勾选要使用的文件；每颗DIE一个Excel。可添加或删除行。
3. “Excel列映射”表格将字段映射到实际表头名称，另可选择工作表和表头行号。长度为μm、角度为度；中心XY成对提供，留空沿用工程中心。未提供真实产线模板，当前适配器支持.xlsx，旧.xls须先转换。
4. 核对DIE数量、并发线程、内存、重试和超时。“校验并创建任务”将按文本封装ID匹配各DIE，拒绝重复、缺测、公式、非有限数值和摆放坐标冲突。全零测量保留。现有任务记录不会覆盖，新建时自动生成独立任务名。
5. “运行 / 继续补算”生成最终OAS。任务记录及最终OAS路径位于工程行下方。改变勾选项/映射/DIE数量后须新建任务。

底部连续进度条叠加当前阶段、真实计数/百分比及用时：输入摘要、Excel读取与汇入、封装匹配、服务准备、并行封装计算、OAS写入、CRC/SHA校验、原子提交。各阶段分别计进度；未知总行数时显示已读取行数，不编造总百分比。长文字自动省略，悬停可查看完整信息。信息输出默认隐藏，通过checkbox展开；隐藏不影响实时文件日志。

## 恢复与数据边界

SQLite WAL/FULL保存输入摘要/源行号、任务状态、回执及事件；输入按文件并行、封装在磁盘关联，提交Future有界，界面每页200条。日志8MiB轮转保留8份备份。停止在安全点取消自己的批次，不强杀几何线程。

崩溃后先核对PID创建时间和冻结工程身份，确认服务已退出才重启；仍存活而无响应时停止并保留任务。未最终提交的批次完整重放；不能按computed计数跳过已丢失几何。新OAS完成计数、CRC、摘要及旧目标保护检查后才原子替换。几何硬失败不通过allow_failed绕过。批量跳过完整显示DRC与概率，输出成功不等于生产签核。

## 手动IPC与自动化

第二页保留15个命令及JSON参数编辑：ping、info、app.start、project.open、project.close、session.prepare、output.configure、preview.submit、export.gds、batch.begin、batch.submit、batch.end、task.get、compute.cancel、app.shutdown。支持异步等待，批次ID/任务ID自动填入模板；项目和输出目录仍限定在所选文件夹。app.start使用同目录启动规则。

自动化兼容原有入口：`python RealDataAnalyse/main.py --import-data import.json --result imported.json`，导入JSON字段为paths、database、die_count、workers及可选profile；随后 `--run config.json --result result.json`，运行JSON字段为database、endpoint、project、output及workers、memory_mb、retries、timeout。EXE接受相同参数。这些无界面接口保持兼容已有完整路径配置。
