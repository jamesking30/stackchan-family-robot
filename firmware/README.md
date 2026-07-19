# StackChan 固件计划

当前真机固件：`1.4.4`。真机已确认为官方 `m5stack-stack-chan`（ESP32-S3、16MB Flash、8MB PSRAM），并已完成两次全 Flash 一致性备份。详细结果见 `docs/hardware-check-2026-07-19.md`。

## 采用路线

- 应用层基线：[uezo/AIAvatarStackChan](https://github.com/uezo/AIAvatarStackChan)（MIT），用于低延迟语音、VAD、表情、口型、摄像头和 OpenClaw 效果。
- 硬件/BSP 基线：[m5stack/StackChan](https://github.com/m5stack/StackChan)（MIT），用于官方板卡驱动、OTA、音频、相机、舵机、触摸和 LED。
- 主机侧基线：[uezo/aiavatarkit](https://github.com/uezo/aiavatarkit)（Apache-2.0）。

本仓库暂不复制这些上游代码。官方仓库提交 `b72b3ede` 已确认包含与真机匹配的板级实现和分区表；后续以锁定提交的 fork/submodule 方式引入，并保留上游许可证与修改记录。

## 刷机前门禁

1. 拍照或记录主控型号、StackChan 套件版本、舵机型号与接线。
2. 通过 USB 识别串口芯片、芯片型号、Flash 大小与 MAC。
3. 使用 `esptool` 读取完整 Flash，保存哈希和恢复命令。
4. 导出 1.4.4 的 Wi-Fi 以外设备配置；密钥不进入 Git。
5. 先在模拟器和开发分区验证，再烧写真机。
6. 验证屏幕、扬声器、麦克风、相机、舵机方向、限位与急停。

刷机时会生成独立的 `hardware-profile.json`，不会根据“StackChan”名称猜测具体 Core2/CoreS3/Dial 等板型。
