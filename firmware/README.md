# StackChan 固件计划

当前真机固件：`1.4.4`。真机已确认为官方 `m5stack-stack-chan`（ESP32-S3、16MB Flash、8MB PSRAM），并已完成两次全 Flash 一致性备份。详细结果见 `docs/hardware-check-2026-07-19.md`。

## 采用路线

- 应用层基线：[uezo/AIAvatarStackChan](https://github.com/uezo/AIAvatarStackChan)（MIT），用于低延迟语音、VAD、表情、口型、摄像头和 OpenClaw 效果。
- 硬件/BSP 基线：[m5stack/StackChan](https://github.com/m5stack/StackChan)（MIT），用于官方板卡驱动、OTA、音频、相机、舵机、触摸和 LED。
- 主机侧基线：[uezo/aiavatarkit](https://github.com/uezo/aiavatarkit)（Apache-2.0）。

官方固件已通过 Git submodule 引入到 `firmware/upstream/stackchan`，锁定提交为 `b72b3ede`。该提交包含与真机匹配的板级实现和分区表。所有直接依赖、官方补丁和 ESP-IDF 版本记录在 `firmware/source-lock.json`，上游许可证和历史保持完整。

## 本地构建

主机工具链基线：

- ESP-IDF `v5.5.4`，提交 `735507283d5b2f9fb363a1901172dbd9e847945d`
- ESP32-S3 GCC `14.2.0`
- CMake `>=3.16` 和 Ninja

默认期望 ESP-IDF 安装在 `~/.espressif/frameworks/esp-idf-v5.5.4`。若位置不同，设置 `STACKCHAN_IDF_ROOT`。

```bash
./scripts/install_firmware_toolchain.sh
./scripts/prepare_firmware.sh
./scripts/build_firmware.sh
```

构建产物写入 `var/firmware-build/m5stack-stack-chan-b72b3ede`，不会污染固件子模块，也不会自动刷机。构建结束后会自动检查板型、Flash 参数、分区表、镜像哈希和分区容量，并生成 `firmware-manifest.json`。需要直接使用 `idf.py` 时，可先执行：

```bash
source ./scripts/firmware_env.sh
```

公共上游源码的项目版本是 `1.4.3`；当前真机出厂镜像是 `1.4.4`。因此本构建用于可审计的硬件/BSP 基线，不能被描述为出厂 `1.4.4` 的逐字节复现。基线仍包含上游服务地址，校验清单会将其标记为 `deployment_ready: false`；接入 Mac 本地网关前不得刷写真机。

面向本项目的局域网产品构建使用：

```bash
./scripts/bootstrap_local_env.py  # 仅首次运行
./scripts/build_product_firmware.sh --gateway-host 192.168.31.65
```

产品构建不会修改上游子模块，也不会自动刷机。生成的设备密钥、固件配置和镜像位于 Git 忽略目录；清单只包含密钥指纹。当前 Mac 网关与产品固件流程见 `docs/m1-local-gateway.md`。

## 刷机前门禁

1. 拍照或记录主控型号、StackChan 套件版本、舵机型号与接线。
2. 通过 USB 识别串口芯片、芯片型号、Flash 大小与 MAC。
3. 使用 `esptool` 读取完整 Flash，保存哈希和恢复命令。
4. 导出 1.4.4 的 Wi-Fi 以外设备配置；密钥不进入 Git。
5. 先在模拟器和开发分区验证，再烧写真机。
6. 验证屏幕、扬声器、麦克风、相机、舵机方向、限位与急停。

刷机时会生成独立的 `hardware-profile.json`，不会根据“StackChan”名称猜测具体 Core2/CoreS3/Dial 等板型。
