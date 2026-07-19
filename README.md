# StackChan Family Robot

面向家庭的本地优先 StackChan 控制系统。Mac 保存角色、四用户记忆、家庭设备权限与 Codex/OpenClaw 任务状态；机器人只负责实时音视频、表情和动作。

当前里程碑是 **M1：真机通信与表情动作**。M0 控制平面已经完成，并新增：

- 中英双语、儿童安全的默认角色；
- 可热更新、带版本与回滚的回答风格；
- 四个独立家庭用户槽位与隔离记忆；
- Codex/OpenClaw 任务状态上报和机器人显示状态；
- 本地管理页、HTTP API、命令行和 MCP 管理入口；
- 管理密钥开关与审计记录；
- 面向 StackChan 固件 1.4.4 的升级边界和联调计划；
- 与官方固件兼容的局域网 WebSocket、心跳、文字、表情和安全舵机控制；
- Codex/OpenClaw 任务状态自动同步到在线机器人；
- 设备专用密钥、Mac 本地 OTA 检查与可审计的产品固件构建。

## 本地启动

```bash
cd stackchan-product
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,mcp]'
cp .env.example .env
stackchan-control
```

打开 <http://127.0.0.1:8765>。默认只监听本机；设置 `ROBOT_ADMIN_API_KEY` 后，所有写操作都要求 `X-Robot-Admin-Key` 请求头。

真机局域网联调不要手工复制空模板，直接执行 `./scripts/bootstrap_local_env.py` 生成私有密钥和 LAN 配置，再按 `docs/m1-local-gateway.md` 构建产品固件。服务监听非本机地址时，管理密钥是强制项。

测试：

```bash
pytest
```

## 管理操作

```bash
robotctl character show
robotctl character style "回答更短，每次最多三句；英文词汇后给出简短中文解释。"
robotctl tasks report --source codex --title "编译固件" --status running --progress 0.4
robotctl tasks list
```

把 MCP 服务接到 Codex 或 OpenClaw：

```bash
stackchan-admin-mcp
```

MCP 服务通过 `ROBOT_CONTROL_URL`（默认 `http://127.0.0.1:8765`）访问控制平面，并沿用 `ROBOT_ADMIN_API_KEY`。

## 仓库结构

- `src/stackchan_control/`：本地控制 API、SQLite 数据层和命令行。
- `src/stackchan_admin_mcp/`：供 Codex/OpenClaw 调整角色、记忆和任务的 MCP 工具。
- `config/seed_character/`：首次启动时写入版本库的默认角色。
- `web/`：非技术用户管理页。
- `firmware/`：真机固件来源、版本约束和刷机说明。
- `docs/`：架构与后续里程碑。

## 数据与隐私

运行数据默认写入 `var/stackchan.db`，不会进入 Git。删除记忆采用可审计的软删除；儿童或未分配用户的“AI 推断记忆”默认进入待审核状态。人脸特征和原始对话不会下发到 ESP32。
