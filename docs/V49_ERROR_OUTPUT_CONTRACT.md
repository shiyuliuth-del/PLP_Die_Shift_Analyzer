# V49 联合接口约定

最新用户要求优先：批量封装存在无法正确生成的连接时，只移除问题线路的失败位置，保留正常部分并继续后续封装。DRC违规需要矩形标记，不因尺寸规则问题删除正常生成的铜。软件异常、无效几何、错误输入和磁盘故障不伪装成可完成的连接问题，仍走有界恢复。

- 客户端batch.begin显式请求`output_policy: "omit_failed_regions"`；服务须返回同策略，旧服务不静默接受。
- 服务完整处理每一姿态实际铜DRC和连接失败范围，不计算无关良率。问题采用当前姿态GDS顶层μm坐标`bounds_um`，提供`die_ids`（1起；仅公共静态问题为[0]）。不能按最近芯片猜测归属。跨DIE问题在各相关DIE标记。
- 已定位且已移除失败位置的回执须明确`partial_output: true`及移除数量/问题数，不能通过只有error字符串的回执推断完成。所有问题保存在分页目录，不因IPC消息限制截断。
- OAS Store可选`error_layer=(layer,datatype)`：默认从65535/0向下选未使用的数值层，写标准LAYERNAME为ERROR，不覆盖源层。`add(..., errors=[{bounds_um,die_ids}],die_count=N)`在封装内按DIE生成子Cell，直接写矩形；共同问题在DIE_0子Cell。原始几何Cell复用保持，层级展开后标记坐标必须与回执/TopCell平移一致。
- 客户端记录“已输出（含问题）”与DRC/未连接数量，允许本封装和后续封装完成；Output仍只保留最终OAS，目录及中间文件在Cache。

原型独立验证：OASIS数字层和名称可同时保存，使用KLayout LayerInfo及官方API合同，真实回读确认ERROR名称与数值层、逐DIE直接矩形、零宽见证至少1DBU、原铜面积和封装平移保持。参考：https://www.klayout.de/doc/code/class_LayerInfo.html 。

客户端接线已提交：协商策略不匹配拒绝启动新批次；回执需要fixed_rdl_drc_checked=true，连接失败还须partial_output=true且omitted_region_count>0，有问题须有error_rectangles。逐项计数与batch.end汇总相等才发布。每包记录区分committed/committed_with_issues，全部输入仍有最终Cell，计划顺序照常继续。

GD55默认Ordinary60为180组独立XYZ，Stress42包括3个严格模式已复现的异网端孔碰接，FailureRecovery6夹有正常前后项；所有新表均已用实际Excel解析器验证。运行层接口已联合合并。已修复裁剪后的DBU退化片，真实客户端Ordinary60、Stress42全量端到端通过；目录资源关闭与Windows最终验收见V49_CLIENT_VALIDATION。
