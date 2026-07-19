# OpenClaw 接入

开发环境已经把 `stackchan-family-admin` 注册到 OpenClaw 的隔离 `--dev` 配置；正式 OpenClaw 网关和现有代理未被修改。

查看：

```bash
openclaw --dev mcp show
```

启动本项目控制服务后，可另开终端启动隔离网关：

```bash
openclaw --dev gateway
```

验证角色、记忆、任务与家庭设备权限后，再把同一 MCP 定义发布到正式配置。正式发布前必须处理现有 OpenClaw 安全审计中的高权限调试开关和未固定版本插件；家庭机器人不继承 `approve-all` 权限。

MCP 工具：

- `get_robot_character` / `update_answer_style` / `rollback_robot_character`
- `list_family_users`
- `search_user_memory` / `remember_for_user` / `approve_child_memory` / `forget_user_memory`
- `report_agent_task` / `list_agent_tasks`

家庭设备动作将在 M4 经 Home Assistant 适配器加入，并按 `tool_policy.yaml` 分级确认。
