# bridge.py JSON-lines 协议基线

## 基线来源

协议原始基线按 2026-09-25 可读取到的 ../bridge.py、../dsh-plugin/lib/index.js 和 ../dsh-plugin/package.json 核对。阶段 3b 在原桥上增量增加了 opt-in Codex 严格备份路径；DSH 插件版本为 1.2.1，桥脚本没有单独的版本号或可确认的 Git 提交号。只读源码与无硬件夹具，未连接串口或实体设备。

## 传输格式

- DSH 以 python -X utf8 -u bridge.py --workspace <路径> --profile <hiwonder|generic> 启动子进程；--profile 默认 hiwonder。标准输入、标准输出使用 UTF-8，每行一条 JSON。
- Host 请求是 JSON 对象：{"id": 1, "cmd": "status", ...参数}。DSH 为每个请求分配整数 id。
- 成功响应：{"id": 1, "ok": true, "data": {...}}。命令失败：{"id": 1, "ok": false, "error": "RuntimeError: ..."}。错误文本由异常类型和消息组成。
- 桥启动时发 {"event":"log","text":"bridge ready, workspace=..."}。控制台与桥日志也会作为 event=console、event=log 消息写到 stdout，可与响应交错。Host 按 id 匹配响应，并单独消费事件。
- 空白输入行会被忽略。JSON 解析失败或请求不是对象时，桥发出没有 id 的 {"ok":false,"error":"bad request: ..."}。因此 Host 无法把这类错误关联到某个待处理请求；DSH 目前会忽略无 id 的普通消息。
- 命令在桥的 stdin 循环中同步处理。桥没有统一的 JSON 请求超时；串口操作各自有底层读取期限。DSH 为命令设置宿主期限（见下表）；超时会终止桥进程、拒绝该调用，以释放串口。桥进程退出时，DSH 清除连接状态并拒绝所有待处理请求。

## 命令与响应数据

所有 data 都嵌在成功响应封套内。以下是 Bridge.handle 的完整命令集合和当前返回字段。

| cmd | 输入参数 | data 形状 |
| --- | --- | --- |
| ports | 无 | {"ports":[{"device":"COM4","description":"...","ch340":true}]}。hiwonder 仅列 CH340；generic 列出全部串口，CH340 排在前面。 |
| connect | 可选 port；省略时自动选择首个匹配串口 | {"connected":true,"port":"COM4","boardInfo":"..."}。连接时会中断程序、执行机型停止动作并探测板信息。 |
| disconnect | 无 | {"connected":false}。关闭前调用机型停止动作。 |
| status | 无 | {"connected":false,"port":"","busy":false,"boardInfo":"","workspace":"..."}。 |
| stop | 无 | {"stopped":true}。注意此字段不代表 generic 机型的电机或舵机已经安全停下，详见下文。 |
| run | 无 | {"ran":true}。尝试软重启 MicroPython 板。 |
| interrupt | 无 | {"interrupted":true}。向 REPL 发送中断并执行 profile 停止动作。 |
| send | line：一行 REPL 文本 | {"sent":true}。REPL 回显与输出通过 console 事件发送。 |
| download | content 必须是非空字符串；可选 run、target、strictBackup | legacy 响应形状保留；strictBackup=true 时额外返回 `targetExisted`、`backupVerified`、`backupPath`、`backupSize`、`backupCrc`。校验失败的 `compileOk` 为实际语法校验布尔值，失败也可能由 size 或 CRC32 不匹配导致。legacy `backup` 可能为空；target 默认值在桥内是 /corex.py。 |
| listfiles | 无 | {"files":[{"name":"main.py","size":N}]}。 |
| readfile | path；可选 `raw=true`、`maxBytes` | legacy：`{"path":"/main.py","size":N,"content":"..."}`。raw：`{"path":"/main.py","size":N,"crc":"XXXXXXXX","contentBase64":"..."}`，用原始 bytes 避免 UTF-8 replacement 解码造成信息丢失。 |
| writefile | path、非空字符串 content；可选 strictBackup、expectedOldCrc | legacy：`{"ok":true,"path":"/x.py","size":N,"crc":"XXXXXXXX"}`。strictBackup=true 时附带严格备份字段；`expectedOldCrc` 仅用于防止 PID 读后目标变化。 |
| deletefile | path；可选 strictBackup | legacy：`{"ok":true,"path":"/x.py"}`。strictBackup=true 时必须先证明目标存在并附带严格备份字段。 |
| capabilities | 无 | Codex 扩展：`{"codexStrictBackup":1,"codexRawFileRead":1}`。版本字段只有显式存在且为整数 1 才被 MCP 接受。 |

板载路径要求以 / 开头，拒绝 ..、引号和反斜杠。文件写入与下载会执行长度、CRC 校验；下载还会做语法校验。阶段 1 协议夹具只验证消息封套和数据形状，不模拟板载操作。

DSH 通过 download 明确传入当前机型的 entry（幻尔通常为 /corex.py，generic 通常为 /main.py）。直接调用桥时若省略 target，桥常量 TARGET 对两种 profile 都是 /corex.py；generic 的 /main.py 依赖 Host 显式传值。

DSH 不传 `strictBackup` 时保留原行为：download 备份读取或本地保存失败会发 console 提示并继续覆盖；legacy `backup` 文件名不证明备份成功。Codex 写删路径必须先请求 `capabilities` 并传 `strictBackup:true`。严格路径通过成功枚举目标父目录证明存在或不存在；父目录枚举失败、原始 bytes 读回/CRC 校验失败、工作区根目录备份创建/flush/fsync/回读校验失败，都会在发送目标 `open(...,'wb')` 或 `os.remove` 前拒绝。目标原本不存在的创建响应明确为 `targetExisted:false, backupVerified:false`；现存目标只有在原始备份逐字节、长度和 CRC 校验成功后才是 `targetExisted:true, backupVerified:true`。strictBackup 之外的 DSH 调用默认行为不变。

## profile 语义差异与源码缺口

- hiwonder 的 stop_motors 会先 Ctrl-C，再发送 EncoderMotor 两路速度归零命令。
- generic 的 stop_motors 只中断正在运行的程序，然后返回；电机和舵机可能保持当前位置。现有 c_stop 对两种 profile 都返回 {"stopped":true} 并发送“电机已停转”控制台文本。这是旧协议的语义不一致，不能把 generic 的 stopped:true 或该文本当作电机安全停转证明。tests/fixtures/stop.*.jsonl 是忠实记录这个 wire 响应的原始样本，测试仅核对线协议。
- interrupt 的响应同样不携带 profile 或停止结果；其控制台文本称“已打断程序并停电机”，但 generic 仍只中断程序。调用方须结合 profile 解读。
- bridge.py 主循环在 stdin EOF 时自然结束，没有显式调用 close_quiet()。子进程退出会由操作系统关闭串口句柄；源码没有在 EOF 路径显式执行停止动作。后续适配器应分别处理请求超时和进程退出，并避免据此假定设备运动已停止。

## DSH 宿主等待期限

这些是 dsh-plugin/lib/index.js 调用 bridgeCmd 时传入的期限，单位毫秒；省略时宿主默认 15000 ms。

| 命令 | 宿主期限 |
| --- | ---: |
| ports | 8000 |
| connect | 30000 |
| disconnect、stop、interrupt | 15000 |
| run | 12000 |
| send | 8000 |
| download | 120000 |
| listfiles | 20000 |
| readfile | 60000 |
| writefile | 120000 |
| deletefile | 15000 |

这些是 DSH 当前宿主行为的基线，不是阶段 1 模拟桥的默认超时，也不替代后续 MCP 宿主的期限设计。

## 无硬件夹具

tests/mock_bridge.py 是独立 JSON-lines 子进程模拟器，不导入 bridge.py、不依赖 pyserial，也不枚举或打开串口。tests/fixtures/ 为 13 个命令分别提供请求与 canned 响应形状；download 另有校验失败响应。模拟器不执行串口、REPL 或板载文件操作。超时场景保持进程存活但不响应；退出场景在收到请求后以非零状态退出。使用方法见 tests/README.md。
