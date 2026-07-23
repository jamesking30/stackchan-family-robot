# StackChan 跨网络连接方案

## 目标架构

StackChan 不再连接 Mac 的局域网地址，而是通过 `wss://` 主动连接一个稳定的
Cloudflare Tunnel 域名。Mac 上的 `cloudflared` 通过出站连接把该域名转发至
`127.0.0.1:8765`，不需要公网 IP、端口映射或调整家庭路由器。

公网入口只匹配以下两个路径：

- `/stackChan/ws`：机器人语音、头像、摄像头和舵机的双向 WebSocket。
- `/v1/device/ota/check`：设备 OTA 检查。

其他路径统一返回 404，因此管理界面、用户记忆、角色配置和任务 API 不会通过
此域名暴露。WebSocket 仍必须提供固件内置的随机设备密钥；该密钥只通过 TLS
传输。远程管理 Mac 应另用 Tailscale、SSH VPN 或 Codex，不与设备公网入口混用。

## Cloudflare 配置

前提：一个由 Cloudflare 托管 DNS 的域名，以及一个命名 Tunnel。

1. 在 Mac 安装 `cloudflared`，登录后创建名为 `stackchan-family` 的 Tunnel。
2. 为 Tunnel 创建 `robot.<你的域名>` DNS 路由。
3. 复制 `deploy/cloudflare/stackchan-tunnel.yml.example` 到
   `~/.cloudflared/config.yml`，填写 Tunnel UUID、凭据路径和域名。
4. 运行 `cloudflared tunnel ingress validate`，确认仅两个路径命中本地服务。
5. 用 `cloudflared service install` 安装为 macOS 登录服务。

Cloudflare Tunnel 支持 WebSocket，并通过 Mac 发起的出站连接工作。生产环境应
使用命名 Tunnel；随机的 `trycloudflare.com` Quick Tunnel 只适合临时验证，
不能写入机器人固件。

## 构建远程固件

```bash
./scripts/build_product_firmware.sh \
  --gateway-url https://robot.example.com
```

生成器会拒绝公网 `http://` 地址、URL 用户名密码、查询参数和路径。产品固件会将
HTTPS 服务地址转换成 `wss://` WebSocket 地址，同时保留 HTTPS OTA 地址和系统
根证书校验。

构建通过后，再通过 USB 刷写生成目录中的 `flash_args`。切换前必须先确认：

- `https://robot.example.com/stackChan/ws` 能完成 WebSocket 升级；
- 无设备密钥时服务返回拒绝，而不是接受连接；
- `/v1/users`、`/v1/voice/state` 等管理路径从公网返回 404；
- Mac 重启后 `cloudflared` 和机器人主服务均自动启动。

## 故障回退

保留上一版局域网固件及其构建清单。如果命名 Tunnel、DNS 或 TLS 不可用，通过
USB 刷回局域网版本即可；设备密钥和用户数据库不需要迁移。
