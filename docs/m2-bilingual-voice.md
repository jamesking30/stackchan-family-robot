# M2 中英语音链路

## 当前实现

M2 采用 Mac 编排、StackChan 实时采播的本地优先结构：

1. Avatar 应用收到 Mac 的 `START_AUDIO_STREAM` 后才打开麦克风。
2. 设备以 16kHz、60ms 单声道 Opus 帧发送音频，并发送设备侧 VAD 开始/结束事件。
3. Mac 只在内存中解码和组成 WAV，请求转写后立即释放原始音频，不写入数据库或文件。
4. Mac 从当前角色版本、当前用户权限和该用户已确认记忆构造提示，经 Responses API 得到短回答。
5. TTS 返回 24kHz PCM，Mac 编码成 60ms Opus 帧并按播放速度下发；回答文字同时显示在脸部界面。
6. 说话期间检测到新的设备侧 VAD 事件会取消当前下发、清空设备播放队列并恢复监听。

默认会话使用 `user-2`（`unassigned`），在人脸识别 M3 完成前不推断成人身份或使用成人权限。角色安全文档始终排在用户话语和记忆之前。

## 模型与可替换配置

| 环节 | 默认模型 | 选择原因 |
|---|---|---|
| 转写 | `gpt-4o-transcribe` | 中英识别准确度优先 |
| 回答 | `gpt-5.6-terra` | 家庭对话的质量、延迟和成本平衡 |
| 合成 | `tts-1` / `alloy` | 低延迟 PCM 输出，便于设备端流式播放 |

当前官方总模型指南把 `gpt-5.6-sol` 定位为复杂推理/编码旗舰，`gpt-5.6-terra` 定位为智能与成本平衡；家庭短对话因此默认使用 Terra。全部模型均可通过 `.env` 更换，无需重新刷机。

```dotenv
ROBOT_VOICE_AUTO_START=false
ROBOT_VOICE_USER_ID=user-2
ROBOT_VOICE_TRANSCRIPTION_MODEL=gpt-4o-transcribe
ROBOT_VOICE_CHAT_MODEL=gpt-5.6-terra
ROBOT_VOICE_TTS_MODEL=tts-1
ROBOT_VOICE_NAME=alloy
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

- OpenAI 密钥只保存在 Mac，不进入固件、WebSocket 帧或 Git。
- 原始 Opus、PCM 和临时 WAV 只存在于内存。
- 当前状态接口只返回本轮转写文字、回答文字、阶段和错误；不返回音频。
- 设备离线时拒绝开始会话；停止和打断会取消后台任务并清空设备播放队列。
- OpenAI 失败时只记录状态码和请求 ID，不记录密钥或原始音频。
- 摄像头流仍保持关闭。

## 尚待现场验证

设备麦克风/VAD/Opus 收包、扬声器播放和真实中英对话需在 M2 固件启动后验证。真实云端回路还要求 `.env` 中存在有效 `OPENAI_API_KEY`。
