# M1 Mac 本地网关

## 已实现能力

Mac 控制服务现在兼容官方 StackChan 固件使用的 `/stackChan/ws` 二进制协议，支持：

- 设备专用密钥认证、单设备会话、5 秒心跳和断线清理。
- 文字、六种受控表情与安全范围内的双舵机动作。
- Codex/OpenClaw 任务上报后自动同步标题、摘要和表情到在线机器人。
- 官方固件需要的本地账户、设备信息、空应用列表和 OTA 检查接口。
- 只保留在线状态与帧计数；Opus 和 JPEG 内容不写入磁盘。

Mac 仍是角色、记忆、权限、任务和密钥的唯一可信主机。固件只包含一枚设备专用局域网凭据，不包含 OpenAI、OpenClaw、家庭成员或对话数据。

## 首次配置

若仓库中还没有 `.env`，执行：

```bash
./scripts/bootstrap_local_env.py
```

脚本会识别 Mac 局域网地址，生成互不相同的管理密钥和设备密钥，并把 `.env` 设为仅当前用户可读。密钥不显示在终端，也不会进入 Git。当前这台 Mac 已完成此步骤，网关地址为 `192.168.31.65:8765`。

启动服务：

```bash
source .venv/bin/activate
stackchan-control
```

因为服务监听局域网，缺少 `ROBOT_ADMIN_API_KEY` 时会拒绝启动；缺少或错误的 `ROBOT_DEVICE_API_KEY` 时，机器人 WebSocket 和设备资料接口会拒绝访问。

## 产品固件构建

```bash
./scripts/build_product_firmware.sh --gateway-host 192.168.31.65
```

构建脚本从私有 `.env` 读取设备密钥，在 `var/firmware-config/` 生成固件覆盖配置，并将官方上游的弱默认鉴权替换为设备专用密钥。原始密钥只存在于私有配置和最终二进制中；构建清单仅保存 SHA-256 指纹。

输出目录：`var/firmware-build/product-m5stack-stack-chan-b72b3ede/`。

只有同时满足以下条件时，`firmware-manifest.json` 才会标记 `deployment_ready: true`：

- 服务和 OTA 地址是私有局域网地址或 `.local` 名称。
- 产品鉴权覆盖组件实际进入链接。
- 设备密钥指纹、板型、分区、写入偏移和镜像校验全部通过。

当前候选镜像已通过上述离线门禁。刷机仍是单独的显式步骤；在真机 WebSocket、屏幕、舵机限位和恢复流程验证前，不自动执行。

## 控制接口

管理接口均沿用 `X-Robot-Admin-Key`：

| 接口 | 用途 | 安全约束 |
|---|---|---|
| `GET /v1/device/state` | 在线、心跳与帧计数 | 不返回音视频内容 |
| `POST /v1/device/text` | 屏幕文字 | 名称 40 字符、正文 240 字符 |
| `POST /v1/device/expression` | 表情 | 只允许预设六种表情 |
| `POST /v1/device/motion` | 头部动作 | yaw `-45°–45°`、pitch `0°–45°`、限速 |
| `POST /v1/device/display/sync` | 同步当前任务卡 | 使用控制平面的显示状态 |

`POST /v1/tasks/report` 在机器人在线时会自动发送任务表情和文字；机器人离线时任务仍正常保存，不会因终端掉线而丢失。
