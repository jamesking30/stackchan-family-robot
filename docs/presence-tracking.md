# 最近人员自动朝向

StackChan 使用 CoreS3 的 GC0308 摄像头进行本地人员定位。摄像头 JPEG
通过现有 WebSocket 进入 Mac 内存队列，由 MediaPipe Face Detector
检测可见人脸；原始帧不写入数据库、日志或文件。

## 工作模式

- 设备连接后等待 5 秒，执行首次全局扫描。
- 每 5 分钟以 yaw `-40°、-20°、0°、20°、40°` 和 pitch
  `5°、20°、35°` 组成二维蛇形扫描，覆盖左右和上下区域。
- 每个角度读取两帧，以人脸框面积和置信度选择距离最近的可见人脸。
- 全局扫描之间每 5 秒短暂采样一次，对当前目标作 yaw 不超过 5°、
  pitch 不超过 4°的小幅修正。
- 人脸中心进入画面中央 6% 死区后不再动作，避免舵机抖动。
- 新人脸的接近分数至少高出当前目标 25% 才切换，避免在多人之间抖动。
- 目标连续丢失 30 秒后回中；未发现人时扫描结束立即回中。
- 正在收音、思考或说话时不扫描。人工舵机命令暂停自动跟踪 60 秒。
- 检测到唤醒词后，在确认音播放且麦克风暂停期间执行短时二维搜索：
  先检查当前视角，再依次检查左右 `18°/40°` 和上下 `10°` 的位置。
- 找到最近人脸后以不超过约 `6°` 的连续小步平滑居中；进入对话监听后
  每约 2 秒低频校正一次。检测到用户正在说话、机器人思考或回答时暂停
  舵机，避免机械噪声污染收音。
- 当前短时搜索无人脸时回到起始角度，并在本轮对话结束后安排完整扫描。

当前安全范围仍为 yaw `-45°–45°`、pitch `0°–45°`。因此“最近人员”
指摄像头和扫描范围内距离最近且脸部可见的人。摄像头帧只在内存中处理。

## “六六”双重识别

开启后，唤醒词触发时会并行取得两项本地证据：

- 从最近约 1.4 秒唤醒音频估计基频和有效浊音比例；
- 对当前画面中距离最近的人脸运行一次年龄估计。

只有“幼儿声线”和“最近人脸为幼童”同时达到阈值，当前唤醒会话才临时
切换为 `user-4 / 六六`。任一证据不足、模型缺失或画面无人时都保持未指定
用户。身份推断随休眠清除，不写入长期记忆；相机帧和唤醒音频均不落盘。

年龄估计使用 InsightFace `buffalo_l` 模型包中的 `genderage.onnx`。
该模型包标注为非商业研究用途；如果项目转为商业产品，需要替换为授权
兼容的模型或自行训练。

## 安装模型

```bash
./scripts/setup_presence_tracking.sh
./scripts/setup_child_identity.sh
```

脚本下载官方 MediaPipe BlazeFace short-range 模型并校验 SHA-256。

## 控制

```bash
robotctl presence state
robotctl presence scan
```

可通过 `.env` 调整：

- `ROBOT_PRESENCE_SCAN_INTERVAL_SECONDS`
- `ROBOT_PRESENCE_TRACKING_INTERVAL_SECONDS`
- `ROBOT_PRESENCE_ACTIVE_TRACKING_INTERVAL_SECONDS`
- `ROBOT_PRESENCE_SCAN_YAW_DEGREES`
- `ROBOT_PRESENCE_SCAN_PITCH_DEGREES`
- `ROBOT_PRESENCE_SERVO_SPEED`
- `ROBOT_PRESENCE_YAW_DIRECTION`
- `ROBOT_PRESENCE_TARGET_SWITCH_RATIO`
- `ROBOT_PRESENCE_WAKE_SEARCH_YAW_OFFSETS`
- `ROBOT_PRESENCE_WAKE_SEARCH_PITCH_OFFSETS`
- `ROBOT_CHILD_IDENTITY_ENABLED`
- `ROBOT_CHILD_IDENTITY_USER_ID`
- `ROBOT_CHILD_IDENTITY_MAXIMUM_AGE`
- `ROBOT_CHILD_IDENTITY_MINIMUM_PITCH_HZ`
