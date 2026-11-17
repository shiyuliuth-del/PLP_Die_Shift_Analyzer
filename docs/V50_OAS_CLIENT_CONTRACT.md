# V50 实测 OAS 与客户端坐标约定

主程序窗口负责将芯片边框按对应DIE的真实姿态及旋转中心变换，生成最终快照；ERROR问题目录增加 `marker_corners_um` 和 `marker_angle_deg`。客户端窗口负责OAS写入及回执兼容，两端在V50统一交付，不修改V49。

- `marker_corners_um` 是当前姿态顶层μm坐标下、有序且非退化的四个矩形顶点，不重复首点。`marker_angle_deg` 是角度说明，不是要求写出端再次施加的变换。旧 `bounds_um` 仍是当前问题的轴对齐范围，不能旋转这个范围来代替真实四角。
- 单DIE问题按主程序确定的姿态方向包住实际见证；跨DIE共同问题只生成一个实际矩形，按原 `die_ids` 写入每个相关DIE子Cell，不能分别再绕各DIE旋转。公共问题仍为DIE_0。
- OAS写出直接保留已变换芯片边框及问题四角，只在TopCell引用加入 `position_um - board_origin`。从此不把新ERROR四角降为轴对齐包围框。
- 格点取整可能带来DBU级角度/边长偏差。必要时沿矩形两条边各向外扩展1 DBU再取整，使极细矩形也保持方向且不漏掉原矩形；不能完整保留时明确报错。循环顶点次序和顺逆时针不影响去重。
- 旧回执没有四角时沿用原bounds向外取整；存在四角但无效时拒绝，不能静默回退丢掉角度。RealDataAnalyse无需重新计算这些几何：任务记录、全部IPC调试与实时会话保留服务返回的新字段。

本窗口一次针对性检查：`test_oas_errors`、`test_oriented_oas_errors`、`test_realdata_errors`共15项正常退出0。独立解析角度/远端旋转中心生成的期望边框，经KLayout整数Region XOR及gdstk读回，覆盖两DIE不同角度、跨DIE共同标记、TopCell平移、阶段/最终OAS、极细标记、非法新字段拒绝、旧格式及客户端持久回执。此项是写入端合成契约检查；真实主程序姿态到快照的整体检查及Windows由V50主窗口合并后统一进行。

## FAIL断开封装层

用户追加：存在不可修复线路断开的封装，最终OAS单独写入命名为FAIL的层，层内只含这些封装的封装边框。以最终`connection_failure_count > 0`判定；普通尺寸DRC不足不进入FAIL，不要求整个封装所有线路都失败。

主程序为ERROR与FAIL各选择独立且不冲突的数值层。`Store(...,failure_layer=(layer,datatype))`声明FAIL层；只对断开封装调用`add(...,failure_outline=case['board'].wkb)`，正常及仅DRC问题封装传None。必须保留真实封装轮廓，不用轴对齐包围盒替代；边框在G_FAIL_封装名子Cell中，随TopCell封装位置平移一次，不随单颗DIE旋转。原图层和ERROR标记分别保留。

每包回执`failed_packages`为0或1，最终为总数，`failure_layer`为命名层描述。客户端逐包核对连接失败与FAIL标记，最终核对汇总，界面/进度/计划显示FAIL封装数。断开但缺少FAIL回执时不发布新结果。崩溃恢复提交也执行该核对，旧版本未含FAIL的断开任务不能通过恢复路径覆盖旧文件，应新建任务；无断开的旧回执仍可恢复。

新增FAIL后19项针对性检查通过；随后补充旧断开任务恢复门禁，FAIL专项5项通过。测试用非矩形封装验证阶段/最终OAS、两种独立读者、不同封装位置、DRC-only排除、空FAIL层、冲突拒绝与旧目标保护。`gui_realdata_errors`已接入原GD55封装轮廓对FAIL的精确XOR，等待V50主窗口的统一集中执行。
