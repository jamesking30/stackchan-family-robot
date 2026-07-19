# 1.4.4 恢复说明

完整备份保存在项目的忽略目录 `var/backups/stackchan-home-01/`，并在 `~/.stackchan/backups/` 保存第二份副本。备份含 NVS，可能包含 Wi-Fi 和设备身份数据，禁止提交到 GitHub。

## 恢复门禁

恢复是写入操作，只在以下条件同时满足时执行：

1. USB 设备仍识别为 `m5stack-stack-chan` / ESP32-S3、16MB Flash。
2. 备份文件大小为 `16777216` 字节。
3. SHA-256 与本机 `manifest.json` 一致。
4. 机器人平放、舵机无遮挡、USB 线稳定。
5. 新固件无法通过 OTA 回滚或常规重刷恢复。

## 恢复命令模板

先校验，不直接复制执行写入命令：

```bash
shasum -a 256 <flash-1.4.4-16mb.bin>
```

确认后，完整恢复命令为：

```bash
.venv/bin/esptool --port /dev/cu.usbmodem101 --baud 460800 \
  write-flash --flash-mode keep --flash-freq keep --flash-size keep \
  0x0 <flash-1.4.4-16mb.bin>
```

恢复后重新读取全 Flash 并做 SHA-256 比较。不要添加 `--erase-all`，不要启用 `--encrypt`。
