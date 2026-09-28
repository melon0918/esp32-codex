# 开发文档索引

独立面板 UI 开发与运行状态见 [当前计划](plan.md)、[网页风格面板开发计划](web-panel-plan.md)、[面板实机点击验收计划](panel-real-click-test-plan.md) 和 [当前状态](status.md)；Agent 控制能力扩展及 AC-00–AC-09 验收见 [Agent 完整控制面板计划](esp32_agent_panel_control_plan.md)。技术规范、需求和独立验收标准分别见 [技术规范](standards.md)、[需求](requirements.md) 与 [独立验收提示](test_prompt.md)。

项目：ESP32 Codex 插件。UI 独立面板步骤 0–10 已交付，UI-11 的 WSL 启动路径修复已通过协议握手验证；UI-12 真实桥租约配置兼容已修复并完成双端 broker 回归；UI-13 已修复面板对不确定控制结果的提示并通过定向回归。RCT-01–06 面板实机点击验收已完成，Agent 确认闭环、MCP 启动入口、工作区同步及窗口生命周期均已核对。AC-00–08 已完成限定范围实施/验收；AC-09 正在按机器人项目 MD-P11 进行单机真机联调。MD-P12 多设备课堂验收仍未批准。当前测试、实机状态和真人确认渠道边界见 [当前状态](status.md) 与当日日志。

| 文件 | 用途 |
| --- | --- |
| [requirements.md](requirements.md) | 已确认的首版范围、功能和验收条件 |
| [architecture.md](architecture.md) | MCP 服务、现有串口桥及与 DSH 的边界 |
| [bridge-protocol.md](bridge-protocol.md) | 从当前桥源码核对的 JSON-lines 命令、响应、事件与兼容差异 |
| [tools.md](tools.md) | MCP 只读、控制与写入工具、策略门禁、来源标记和运行说明 |
| [safety-and-design.md](safety-and-design.md) | 操作确认、串口占用、设备安全和工具设计规范 |
| [implementation-plan.md](implementation-plan.md) | 小步执行顺序、每阶段交付物和通过条件 |
| [web-panel-plan.md](web-panel-plan.md) | 网页风格面板 UI-1 至 UI-6 交付与验收 |
| [esp32_agent_panel_control_plan.md](esp32_agent_panel_control_plan.md) | Agent 完整控制面板 AC-00 至 AC-09 开发计划、接口合同和验收状态 |
| [../devlog/README.md](../devlog/README.md) | 每日开发日志格式和自动记录规则 |

现有实现只作为行为参考：../dsh-plugin/ 是 DSH 插件，../bridge.py 是 Python 串口桥。Codex 插件在本目录独立开发；若规格变化，先更新对应文档，再实现。
