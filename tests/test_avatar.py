import asyncio

from stackchan_control.avatar import (
    ANIMATION_FILES,
    AVATAR_FILES,
    AvatarController,
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
