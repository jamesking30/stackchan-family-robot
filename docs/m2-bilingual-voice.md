# M2 中英语音链路

## 当前实现

M2 采用 Mac 编排、StackChan 实时采播的本地优先结构：

1. Avatar 应用收到 Mac 的 `START_AUDIO_STREAM` 后才打开麦克风。
2. 设备以 16kHz、60ms 单声道 Opus 帧发送音频，并发送设备侧 VAD 开始/结束事件。
3. Mac 在内存中解码音频；本地 Whisper 使用系统临时目录完成转写，任务结束立即删除临时 WAV。
4. Mac 从当前角色版本、当前用户权限和该用户已确认记忆构造提示，仅把文字发送给 DeepSeek Chat Completions 得到短回答。
5. macOS 系统语音在本地生成 24kHz PCM，Mac 编码成 60ms Opus 帧并按播放速度下发；回答文字同时显示在脸部界面。
6. 说话期间检测到新的设备侧 VAD 事件会取消当前下发、清空设备播放队列并恢复监听。

默认会话使用 `user-2`（`unassigned`），在人脸识别 M3 完成前不推断成人身份或使用成人权限。角色安全文档始终排在用户话语和记忆之前。

## 模型与可替换配置

| 环节 | 默认实现 | 选择原因 |
|---|---|---|
| 转写 | whisper.cpp `ggml-small.bin` | Apple Silicon 本地运行，中英自动识别，录音不出主机 |
| 回答 | `deepseek-v4-flash` 非思考模式 | 家庭短对话延迟和成本优先 |
| 合成 | macOS `Tingting` / `Samantha` | 本地中英文语音，不依赖云端音频接口 |

模型和本地语音均可通过 `.env` 更换，无需重新刷机。机器人对话明确关闭 DeepSeek 思考模式，避免短回答消耗不必要的推理时间。

```dotenv
ROBOT_VOICE_AUTO_START=false
ROBOT_VOICE_USER_ID=user-2
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
ROBOT_VOICE_WHISPER_BINARY=whisper-cli
ROBOT_VOICE_WHISPER_MODEL=var/models/ggml-small.bin
ROBOT_VOICE_ZH_NAME=Tingting
ROBOT_VOICE_EN_NAME=Samantha
```

## 管理命令

```bash
robotctl voice state
robotctl voice start --user user-2
robotctl voice interrupt
robotctl voice stop
```

`robotctl voice say "你好，请介绍一下自己"` 可绕过麦克风，仅验证回答与机器人播放。管理接口继续要求 `X-Robot-Admin-Key`。

## 固件补丁与构建

固件音频改动保存在 `firmware/product_patches/m2-avatar-audio.patch`。产品构建脚本会在来源锁验证后临时应用补丁，并在成功、失败或中断时自动撤销，避免锁定上游目录长期处于脏状态。

```bash
./scripts/build_product_firmware.sh --gateway-host 192.168.31.65
```

当前 M2 镜像 SHA-256 为 `6f1dbc1e100f48b2de8648a49c0095783f8b3a1d56e1e13300de2fcd84224e10`，大小 `0x39cb70`，仍有 27% OTA 应用分区余量。资源镜像未变化。

## 隐私与故障边界

- DeepSeek 密钥只保存在 Mac，不进入固件、WebSocket 帧或 Git。
- 原始 Opus 和 PCM 不进入数据库；Whisper 与系统语音使用的临时文件在每次调用结束时自动删除。
- DeepSeek 只接收转写文字、角色规则和当前用户已确认的少量记忆，不接收原始音频。
- 当前状态接口只返回本轮转写文字、回答文字、阶段和错误；不返回音频。
- 设备离线时拒绝开始会话；停止和打断会取消后台任务并清空设备播放队列。
- DeepSeek 失败时只记录状态码、错误代码和请求 ID，不记录密钥或原始音频。
- 摄像头流仍保持关闭。

## 尚待现场验证

设备麦克风、主机侧 VAD 和 Opus 上行已通过真机验证；DeepSeek V4 Flash、本地中文 TTS 和本地 Whisper 回读也已分别通过。剩余验收项是把完整回答通过 StackChan 扬声器播放，并完成连续中英切换与打断测试。
