import asyncio

from stackchan_control.avatar import (
    ANIMATION_FILES,
    AVATAR_FILES,
    AvatarController,
    IDLE_GESTURES,
    validate_esp_avatar,
)
from stackchan_control.gateway import MessageType
from stackchan_control.settings import PROJECT_ROOT


def test_elysia_avatar_assets_are_esp_decoder_compatible():
    assets_dir = PROJECT_ROOT / "assets/avatars/elysia/v1"

    for filename in set(AVATAR_FILES.values()) | set(ANIMATION_FILES.values()):
        validate_esp_avatar((assets_dir / filename).read_bytes())


class RecordingGateway:
    def __init__(self):
        self.messages: list[tuple[MessageType, bytes]] = []

    async def send(self, message_type: MessageType, payload: bytes = b"") -> None:
        self.messages.append((message_type, payload))


def test_emotion_change_uses_three_blink_transition_frames():
    gateway = RecordingGateway()
    controller = AvatarController(
        gateway, PROJECT_ROOT / "assets/avatars/elysia/v1"
    )

    async def run():
        await controller.show("neutral")
        await controller.show("angry")

    asyncio.run(run())

    assert [message_type for message_type, _ in gateway.messages] == [
        MessageType.JPEG,
        MessageType.VIDEO_MODE_ON,
        MessageType.JPEG,
        MessageType.VIDEO_MODE_ON,
        MessageType.JPEG,
        MessageType.VIDEO_MODE_ON,
        MessageType.JPEG,
        MessageType.VIDEO_MODE_ON,
        MessageType.JPEG,
        MessageType.VIDEO_MODE_ON,
    ]


def test_idle_gesture_returns_to_neutral():
    gateway = RecordingGateway()
    controller = AvatarController(
        gateway, PROJECT_ROOT / "assets/avatars/elysia/v1"
    )

    async def run():
        await controller.show("neutral")
        gateway.messages.clear()
        return await controller.play_idle_gesture("hair_touch")

    assert asyncio.run(run()) is True
    assert controller.current_emotion == "neutral"
    assert len(gateway.messages) == len(IDLE_GESTURES["hair_touch"]) * 2


def test_idle_gesture_does_not_override_non_idle_expression():
    gateway = RecordingGateway()
    controller = AvatarController(
        gateway, PROJECT_ROOT / "assets/avatars/elysia/v1"
    )

    async def run():
        await controller.show("thinking")
        gateway.messages.clear()
        return await controller.play_idle_gesture("blink")

    assert asyncio.run(run()) is False
    assert gateway.messages == []
