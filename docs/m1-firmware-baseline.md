# M1 固件构建基线

## 目标

在不写入真机的前提下，建立可审计、可重复的官方 `m5stack-stack-chan` 固件构建流程，为后续接入 Mac 对话网关、状态显示和 OTA 开发提供安全基线。

## 锁定版本

| 项目 | 版本或提交 |
|---|---|
| 板型 | `m5stack-stack-chan` |
| m5stack/StackChan | `b72b3ede38b32d54f0b6ba51c62cfcef2ec3ae1e` |
| ESP-IDF | `v5.5.4` / `735507283d5b2f9fb363a1901172dbd9e847945d` |
| ESP32-S3 GCC | `14.2.0` |
| 上游项目版本 | `1.4.3` |
| 真机出厂版本 | `1.4.4` |

完整直接依赖提交及补丁信息见 `firmware/source-lock.json`。ESP-IDF Component Manager 的传递依赖由上游提交内的 `firmware/dependencies.lock` 锁定。

## 硬件一致性门禁

构建必须保留以下已从真机核对的参数：

- ESP32-S3、16MB QIO Flash、8MB PSRAM。
- 双 OTA 分区；应用偏移 `0x20000`，每个应用分区 `0x4f0000`。
- `assets` 分区偏移 `0xA00000`、大小 `4MB`。
- 构建、检查和备份阶段均不执行 `idf.py flash`。

## 构建流程

```bash
./scripts/install_firmware_toolchain.sh
./scripts/prepare_firmware.sh
./scripts/build_firmware.sh
```

准备脚本会完成四项检查：官方子模块提交、所有直接依赖提交、官方 xiaozhi 补丁、ESP-IDF 精确提交。任意一项漂移都会停止构建。

输出目录位于 `var/firmware-build/m5stack-stack-chan-b72b3ede`，由 Git 忽略。刷机必须作为独立、显式且有恢复验证的后续步骤进行。

构建后的自动检查包括：

- Bootloader 和应用镜像均为有效 ESP32-S3 镜像。
- 写入偏移固定为 `0x0`、`0x8000`、`0xd000`、`0x20000` 和 `0xA00000`。
- 生成分区表的 SHA-256 与已核对真机布局一致。
- 当前应用大小为 `0x39c4e0` 字节，OTA 应用分区剩余约 27%。
- 上游服务地址尚未切换为 Mac 本地网关，因此清单明确标记 `deployment_ready: false`。

`sdkconfig` 选择 QIO 并启用启动时自动探测；ESP-IDF 因此在写入参数中保守使用 DIO 头部，Bootloader 启动后再探测并切换。该行为与真机读取到的 QIO Flash 模式不冲突。

## 已知差异

真机 `1.4.4` 的公开源码尚未包含在锁定的上游提交中；当前公共源码声明版本为 `1.4.3`。我们保留两份一致的 16MB 出厂 Flash 备份作为恢复依据，但不把公共源码构建结果声称为出厂镜像复现。
