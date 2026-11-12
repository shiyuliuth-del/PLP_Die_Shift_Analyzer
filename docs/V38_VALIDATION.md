# V38 验证与交付边界

起点：main `a82099e`，已包含 PR #20 的全部上传分支。远端最初只有 main；旧本地空树合并包装提交 2c41240 仅保留历史，未覆盖脏工作树。集成入口为 [PR #21](https://github.com/shiyuliuth-del/PLP_Die_Shift_Analyzer/pull/21)。

## 本轮检查

- 当前核心：295项，293通过、2项条件跳过。新回归覆盖名义共同端点、27种单DIE极端、独立拖动、跨角吸附、孔盘点模式、反向限制、斜向接缝、源铜与孔洞、两种包围量、缓存不可变、A/B/C开放轮廓、失败保护和旧工程字段。
- Qt真实事件：包含18组现行GUI回归。新V38组验证两端实际鼠标拖动、后台尚未完成时连续编辑、实时开关即时生效、Escape取消、工程保存/恢复、另一姿态复用、层序与合并开关。旧专属预演分析/单刀断言明确退役，不计通过，详见 [合同迁移清单](V38_TEST_MIGRATION.md)。
- 干净源码聚合检查包含上述核心与GUI、两种本机原生几何后端、17个工程CLI；逐例检查已验证组合数等于完整离散目录数。机器结果见 [package_verification.json](package_verification.json)。
- Windows clean/with-cupy 两组必须在最终源代码提交通过，包含源ZIP、BUILD.bat、临时单文件EXE与RUN.bat。准确SHA和实际状态查 [PR Checks](https://github.com/shiyuliuth-del/PLP_Die_Shift_Analyzer/pull/21/checks)。不上传临时EXE，也不把托管Windows写作RTX A5000物理设备验收。

## GD55实图

[机器记录](../results/v38_gd55_reconstruction.json)包含源图SHA-256、运行源码摘要、硬件、冷计算、8种±35 μm/±1°组合、相同端点验证、逐帧合法几何、缓存命中与局部拖动耗时。工艺为15/15/15 μm。代码摘要可用所有 `die_shift_studio/*.py` 按路径排序后拼接计算SHA-256重核。

数值字段 `valid` 只表示当前构造通过几何有效性、分量、孔洞与反折检查；不表示完整线宽/线距DRC通过，更不是生产良率。仍有复杂拓扑、共享端和空间不足位置退回源图，并明确显示原因。全输入包络不能证明间距时保留提示。

DXF使用同一完成状态实际导出并回读：只含开放LWPOLYLINE，不含HATCH；固定RDL真实边界在A/B/C中分段，其他图层仍保留。失败区域按原图B层保留并在运行信息说明数量。没有将私有GDS或生成DXF提交到仓库。

复算：

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
QT_QPA_PLATFORM=offscreen python3 tests/gui_v38.py
python3 tools/package_release.py verify-source
python3 tools/benchmark_v38.py --gds /path/to/GD55_9-21.gds --dxf /tmp/GD55_ABC.dxf
```

正式交付为 [V38 Release](https://github.com/shiyuliuth-del/PLP_Die_Shift_Analyzer/releases/tag/V38) 的源码ZIP与SHA-256。包内只有运行/构建所需源码及RUN.bat、BUILD.bat，无图片、GDS和EXE。发布时回下载资产并核对CRC、7-Zip、SHA-256及自动源码归档；最终文件校验值以Release为准。
