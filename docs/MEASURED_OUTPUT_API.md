# 实测输出本地 IPC 接口（V47，协议 1）

用于其他 Python 程序控制 **已经打开的 Die Shift Analyze 窗口**，或启动一个新窗口。输入长度及坐标单位为 **μm**，接口角度为 **度**。所有请求/响应均为 JSON；不用网络端口、HTTP、pickle、远程代码或 shell 指令。

## 开启与锁定

1. 打开 GDS/OAS 和工程，设置用途、层序、工艺、旋转中心及共用动区。
2. 输出 → 实测输出：设置输出目录、最大内存 MB、最大线程（两者默认0，表示自动）。
3. 勾选“实测输出”。先自动保存当前工程；未命名工程在 Jobs 自动命名。保存失败不开接口。只建立连接快照并等待指令，不运行完整方案评估、良率或重构预演。未打开工程也可开启；计算中开关置灰，任务结束后自动启用。
4. 界面显示本地入口 JSON 的路径。默认在用户私有配置目录；Linux 使用 Unix domain socket，Windows 使用命名管道，均以随机 HMAC 密钥认证。此 JSON 含密钥，**不要提交 Git、分享或放到多人可读目录**。不对外监听 TCP。
5. 开启期间图层用途、工艺、偏移、动区拖动/Q 编辑等受锁定。滚动画布查看不改变冻结参数。关闭后等待安全取消完成，恢复工程参数和零位预览，重新允许人工编辑。开关状态不会随工程自动开启。

`info.result.ready=true` 仅表示有效工程已冻结，路径在首指令懒建（并发共享），不表示全姿态或尺寸DRC已通过。之后才可提交姿态；preparing_task给出准备任务，last_error说明准备失败原因。命令都在同一 GUI 所属上下文执行；Qt/Matplotlib 仅在主线程更新。工程切换需要先结束/取消批次。保存输出配置时保留普通工程的叠加开关和有效历史结果，不把临时实测旋转中心/姿态写成普通评估缓存。

自动启动或切换工程时必须明确TopCell，多个顶层且工程未保存选择会返回错误，不弹出等待人工操作的选择框。读取失败可查询last_error，再project.close或project.open恢复；不要继续对ready=false的工程发送姿态。关闭实测输出后恢复普通手动TopCell选择。

## Python 与命令行

源码包内有仅依赖标准库的客户端：

```python
from die_shift_studio.local_ipc import call
from die_shift_studio.ipc_client import wait

endpoint = r"C:\work\endpoint.json"  # 使用界面实际显示的路径
r = call(endpoint, "info")
print(r)
r = call(endpoint, "preview.submit", {"dies": [
    {"die": 1, "dx_um": 3.5, "dy_um": -2, "angle_deg": 0.1,
     "rotation_center_um": [1200, 900]}
]})
if r["status"] == "accepted":
    result = wait(endpoint, r["task_id"], timeout=3600)
    print(result)
```

命令行示例（从源码根目录运行；复杂 JSON 建议放文件中，避免 Windows 引号转义）：

```text
python -m die_shift_studio.ipc_client --endpoint C:\work\endpoint.json info
python -m die_shift_studio.ipc_client --endpoint C:\work\endpoint.json --wait preview.submit --args-file C:\work\pose.json
python -m die_shift_studio.ipc_client --endpoint C:\work\endpoint.json output.configure --args-file C:\work\output.json
```

接口返回格式：

```json
{"id":"与请求相同","status":"accepted","task_id":"任务编号"}
```

短命令直接返回 `status=completed,result={...}`。错误返回 `status=error,error={"type":"...","message":"..."}`。`task.get` 的外层 completed 表示查询完成；**真正任务状态在 result.status**，可能为 queued/running/completed/error/cancelled。只有任务 completed 才使用 result。超时仅停止客户端等待，不自动取消软件任务。最近 10000 个已结束任务可查询；控制端应及时保存结果。批次内部独立维护计数，历史淘汰不影响结束判断。

## 命令表

| 命令 | args | 完成含义 |
|---|---|---|
| `ping` | `{}` | 协议、开启状态、ready |
| `info` | `{}` | 当前工程、源图纸、TopCell、DIE数量/中心/边框、封装边框、用途、工艺、当前实测姿态/中心、输出配置及正在运行任务 |
| `project.open` | `{"path":"工程绝对路径.dshift.json"}` | 自动保存当前工程，打开/切换目标，冻结参数和连接快照；异步任务 |
| `project.close` | `{}` | 保存并关闭工程，保留 IPC；无工程不能提交姿态 |
| `session.prepare` | `{}` | 显式准备名义路径（可选预热，不做评估/良率）；异步任务 |
| `output.configure` | `{"output_directory":"已有目录","max_threads":4,"max_memory_mb":1024}` | 修改/记住输出配置并保存工程；同一开启会话内跨工程保持目录/限额，任务/批次运行时禁止更改 |
| `preview.submit` | `{"dies":[...]}` | 单姿态重构完成并在窗口显示；返回连接失败明细与规则检查范围 |
| `export.gds` | `{"filename":"sample.gds"}` | 输出最近完成的实测姿态；目录沿用配置，无需重复输入 |
| `compute.cancel` | `{}` | 确认收到全计算取消请求；原生调用在安全返回点退出，查询 info.running 直到清空 |
| `app.shutdown` | `{}` | 确认收到关闭请求；取消并等待安全退出；客户端可观察入口消失/进程退出 |
| `task.get` | `{"task_id":"..."}` | 查询一个任务的状态、进度、结果或错误 |

`preview.submit` 必须提供全部 DIE，每颗恰好一次，编号从1起。每行必须有 die、dx_um、dy_um、angle_deg；rotation_center_um 可省略，省略时使用工程旋转中心，提供时为**绝对中心坐标**，并非相对芯片中心。不同姿态/中心使用不同计算身份，不得复用另一中心的型变。手动指定的实测偏移可超过工程概率范围；此时是给定姿态几何结果，不代表原工艺包络已验证。

V44 在姿态生成阶段检查真实铜的连通、端孔接触和异网隔离。无法通过时任务报错并保留上一完成状态/旧输出；`allow_failed:true` 不再绕过此电连接门禁。尺寸规则不足可保留连通补偿铜并返回诊断，警告图形不作为金属输出。规则违规与连接失败是不同概念；文件不是生产签核。输入文件只读，禁止覆盖源图纸，写出/校验后才原子替换目标。GDS坐标必须落在32位DBU范围，图层/Datatype必须在0～65535；超出则报错并保留旧文件，不能静默截断，可改用批量OAS。

### 启动与退出

软件未启动时无法向它发送 IPC。客户端 `app.start` 是本地进程启动器，支持 main.py 或目标 Windows 上自行构建的 EXE，不执行任意 shell 文本：

```text
python -m die_shift_studio.ipc_client --endpoint C:\work\endpoint.json app.start --args-file C:\work\start.json
```

start.json：`{"app":"C:/DSA/main.py","project":"C:/work/gd55-1105.dshift.json"}`。

也可直接 `python main.py --project <工程> --measured-output --ipc-endpoint <入口JSON>`。启动回执表示 IPC 已开放；仍须查询 ready，不能把进程存在当作工程准备完成。停止命令为 compute.cancel；退出为 app.shutdown。用户手动取消/关闭同样使相关任务返回取消，不能让后到结果覆盖新画面。

## 批量 OAS：开始 → 并发提交 → 结束

```python
from concurrent.futures import ThreadPoolExecutor
from die_shift_studio.local_ipc import call
from die_shift_studio.ipc_client import wait

call(endpoint, "output.configure", {"output_directory": "C:/work/out",
                                   "max_threads": 8, "max_memory_mb": 2048})
begin = call(endpoint, "batch.begin", {"filename": "lot.oas"})
# 必须先检查 completed，再提交。
batch_id = begin["result"]["batch_id"]
def submit(i, dies, xy):
    r = call(endpoint, "batch.submit", {"batch_id": batch_id, "cell": f"Package_{i:06d}",
                                       "position_um": xy, "dies": dies})
    # Busy 可按 error.retry_after_ms 延时重试；不要无限无间隔重发。
    return wait(endpoint, r["task_id"])
# 通过 ThreadPoolExecutor 并发调用 submit；核对每项结果。
end = call(endpoint, "batch.end", {"batch_id": batch_id})
result = wait(endpoint, end["task_id"])
```

- batch.begin 返回 batch_id、实际线程、内存预算、队列容量。并发接收不等于无限启动计算；活动任务受硬件、用户上限及每任务内存估计共同限制，内层不再套线程池。
- batch.submit 和 batch.end 必须带 begin 返回的 batch_id，防止取消后旧请求混入下一批次。batch.submit 还必须有唯一 cell、封装 position_um=[x,y]、全部 dies。Cell 名为1～120个ASCII字母/数字/下划线/短横线；TopCell 与 G_ 前缀保留。坐标以**名义封装边框外接矩形的左下角**为放置基准：从info.board_bounds_um取xmin、ymin，实例平移为(position_x−xmin, position_y−ymin)。TopCell 将每个封装 Cell 放到给定位置；芯片自己的偏移相对封装独立计算。
- 每个封装不刷新窗口，不计算整包概率、宽度分析或完整 DRC 标记。使用同一连接样式/边界设置的几何求解器，仍验证几何与连接；返回 `fixed_rdl_drc_checked=false`。不能把快速结果当成完整 DRC 验收。
- V44 任何未连接、拓扑改变或未消除异网接触的姿态均失败。`allow_failed` 保留旧客户端字段兼容，但不能放过电连接或无效多边形。批量仍跳过完整尺寸DRC，连通通过不代表工艺签核。
- batch.end 停止接收新任务，等已接收任务结束。若任何项失败/取消，最终文件不替换；先查错误，修正后新开批次。不要重复使用已经占用的 Cell 名试图覆盖失败项。
- 内存MB按2²⁰字节计。最大内存是**批量计算的增量预算**，不含程序/原工程的基础占用。估算控制并发；进度安全点检查 RSS，超预算先暂存并报错。原生函数单次调用可能短暂超过预算，Python 不能安全强杀它；不是操作系统硬内存隔离。
- 几何结果按块流式写入同一个临时 OAS，达到预算时刷盘；私有 SQLite 仅保存去重索引与封装位置。结束时补齐 TopCell 和校验尾记录，而非逐姿态重复写整个图纸。相同的量化图形（含平移等价图形）复用内容寻址 Cell，避免重复不动铜/孔盘。这里复用的是物理几何，不保留原设计的 Cell 名称、文本、属性或历史层级。
- 最终只有配置目录下的一个 OAS；临时 OAS 与索引库安全取消/正常结束后清理。进程崩溃可能留下 `.dsa-batch-*` 临时目录，确认实例退出后可删除。CRC通过后原子替换，旧目标在取消和失败时保留。
- 一个批次冻结一个工程与工艺。不能同时切换工程、变更输出参数、交互预览或导出单姿态；先end或cancel。相同网络/状态必须由同一快照解释。

## GD55-1105 验证脚本

独立脚本为 `verify_measured_gd55.py`（仓库 tools 下，Release 单独提供）。只依赖标准库控制软件；若调用端已有gdstk，还会独立回读GDS/OAS。

```text
python verify_measured_gd55.py --app C:\DSA\main.py --project C:\work\gd55-1105.dshift.json --endpoint C:\work\ipc.json --output C:\work\verification --count 8
```

验证工程自动保存/准备、查询、全部芯片姿态与中心、单GDS输出、并发批量、共用Cell、TopCell坐标、取消和退出。输出verification.json保留每项结果；为了验证含实际不适配位置的图纸，其批量显式使用诊断模式，**验证通过仅证明接口/文件合同，不证明全部线路工艺合格**。脚本默认退出自己启动的软件；--keep-open保留窗口。源GDS/OAS不修改。

OAS基本记录编码参考 [gdstk OAS写入实现](https://github.com/heitzmann/gdstk/blob/main/src/library.cpp) 与 [整数/点列编码](https://github.com/heitzmann/gdstk/blob/main/src/oasis.cpp)；本项目仅实现显式名、绝对坐标、多边形、引用和压缩块的输出子集，以两种独立读者复验。

## V44 配套客户端与恢复扩展（仍为协议1）

联合源码包附RealDataAnalyse GUI和GD55案例，操作见[客户端说明](../RealDataAnalyse/README.md)。15个指令均可手动调试；“大量封装并行处理”对应batch.begin/submit/end完整流程，不是单次preview.submit。

`call(..., request_id=...)`允许通信重试复用同ID；命令回执去重保留最近10000项，Busy不缓存。超时不等于失败，控制端必须检查状态或放弃整个未提交批次，不能盲目生成新ID重复提交。同ID只能对应同一命令和参数。

info新增instance_id、session_identity、batch_owner；冻结身份包括软件版本/源几何与Q节点/TopCell/层序用途/运动归属/参数设置。batch.begin可提供32位小写十六进制client_id，info返回以辨认恢复归属。不提供时保持原协议行为。入口JSON新增process_created与instance_id，不得记录token；PID重用不能当旧服务仍在。session.prepare在任何批次存续期间拒绝。

客户端故障恢复只处理自己的批次；原临时几何在服务进程崩溃后不可靠，完整重放未提交批次。正常结果不allow_failed；超限内存可降低并发重试。服务存活但无响应时不强杀/不启动重复实例。当前客户端不会扫描其他安装路径。此扩展不改变批量跳过完整DRC的范围。

## V45资源与交互DRC

max_threads=0及max_memory_mb=0均表示自动（默认）。自动内存预算取当前可用内存35%，下限128 MB；显式预算不高于该可用内存预算。V47起preview.submit与batch.submit均为按指令的快速铜生成，保留连通/拓扑/异网隔离/禁布区门禁，不计算完整显示DRC或概率良率；普通交互的“显示接触位置”仍执行当前实际铜全层规则检查。

## V46阶段文件

每批次生成独立的`<输出名>.<批次随机ID>.partial.oas`。首个成功封装、每64个封装及内存刷写阶段均原子发布带TopCell/CRC的完整阶段文件；失败或取消不会删除该文件，也不替换最终目标。batch.submit回执含checkpoint和checkpoint_cells，后者才是已落盘数量，不能把正在运行或排队数当作已落盘。阶段文件可直接读取，RealDataAnalyse现有重启逻辑仍完整重放未提交批次，不假装支持自动从阶段几何续接。

### V47按需执行

`main.py --measured-output --ipc-endpoint <文件>`及`app.start`允许省略project；接口开启后等待project.open。带project启动也不自动评估或预演。preview.submit与batch.submit只执行指定姿态的铜生成和连通/拓扑/隔离/禁布区检查，不启动全局尺寸DRC、概率或良率，返回fixed_rdl_drc_checked=false。禁布区允许共边，不能正面积进入；不可修复时错误回执并保留旧输出。先前调用方式和协议1兼容。

## V48 多实例与客户端记录

多个预演窗口手动开启实测输出时，首个使用默认endpoint.json；该入口被占用时，后续窗口在同一私有IPC目录创建带进程ID/随机标识的独立入口，并在窗口显示实际路径。显式指定--ipc-endpoint仍要求唯一，不覆盖别的实例。

RealDataAnalyse以工程路径和info.instance_id匹配实例，计划使用独立入口；多实例下不能只按“第一个进程”发送命令。新“实时命令信息”展示该客户端实际的请求、回复、轮询与异常，界面和队列有上限，完整脱敏报文按项目轮转记录。客户端任务超时默认0表示无计算超时，连接/认证仍有限时和有界恢复。

RealDataAnalyse生产批次将output.configure指向项目Cache/Batches；OAS完整数量、CRC与SHA验证完成后发布到Output。Output仅保留一个最终OAS，旧结果和旧附属文件归档Cache/History。手动调试默认使用Cache/Manual；服务协议的显式输出目录参数仍有效。
