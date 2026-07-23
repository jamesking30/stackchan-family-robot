from __future__ import annotations

from pathlib import Path

from .gateway import MessageType, StackChanGateway


AVATAR_FILES = {
    "neutral": "neutral.jpg",
    "listening": "listening.jpg",
    "thinking": "thinking.jpg",
    "doubt": "doubt.jpg",
    "happy": "happy.jpg",
    "excited": "excited.jpg",
    "concerned": "concerned.jpg",
    "angry": "angry.jpg",
}

AVATAR_ALIASES = {
    "focused": "thinking",
    "apologetic": "concerned",
    "sad": "concerned",
    "sleepy": "neutral",
    "task_running": "thinking",
    "task_complete": "happy",
    "task_failed": "concerned",
}


class AvatarAssetError(RuntimeError):
    pass


def esp_jpeg_metadata(image: bytes) -> tuple[int, int, tuple[int, ...]]:
    """Return dimensions and sampling factors for an ESP-compatible baseline JPEG."""
    if not image.startswith(b"\xff\xd8"):
        raise AvatarAssetError("avatar asset is not a JPEG")
    offset = 2
    while offset + 4 <= len(image):
        if image[offset] != 0xFF:
            offset += 1
            continue
        marker = image[offset + 1]
        if marker in {0xD8, 0xD9}:
            offset += 2
            continue
        length = int.from_bytes(image[offset + 2 : offset + 4], "big")
        if length < 2 or offset + 2 + length > len(image):
            break
        if marker == 0xC0:
            if length < 17 or image[offset + 9] != 3:
                break
            height = int.from_bytes(image[offset + 5 : offset + 7], "big")
            width = int.from_bytes(image[offset + 7 : offset + 9], "big")
            sampling = tuple(image[offset + 11 + index * 3] for index in range(3))
            return width, height, sampling
        offset += 2 + length
    raise AvatarAssetError("avatar asset is not a baseline JPEG")


def validate_esp_avatar(image: bytes) -> None:
    width, height, sampling = esp_jpeg_metadata(image)
    if (width, height) != (320, 240):
        raise AvatarAssetError(
            f"avatar asset must be 320x240, got {width}x{height}"
        )
    if sampling != (0x22, 0x11, 0x11):
        raise AvatarAssetError(
            "avatar asset must use YUV 4:2:0 sampling for the ESP JPEG decoder"
        )


class AvatarController:
    def __init__(self, gateway: StackChanGateway, assets_dir: Path) -> None:
        self.gateway = gateway
        self.assets_dir = assets_dir
        self.current_emotion: str | None = None

    def resolve(self, emotion: str) -> tuple[str, Path]:
        normalized = AVATAR_ALIASES.get(emotion, emotion)
        filename = AVATAR_FILES.get(normalized)
        if filename is None:
            normalized = "neutral"
            filename = AVATAR_FILES[normalized]
        path = self.assets_dir / filename
        if not path.is_file():
            raise AvatarAssetError(f"avatar asset is missing: {path}")
        return normalized, path

    async def show(self, emotion: str) -> str:
        normalized, path = self.resolve(emotion)
        image = path.read_bytes()
        try:
            validate_esp_avatar(image)
        except AvatarAssetError as exc:
            raise AvatarAssetError(f"{path}: {exc}") from exc
        # Load the frame while the overlay is hidden, then reveal it. This avoids
        # displaying an old expression while the new JPEG is being decoded.
        await self.gateway.send(MessageType.JPEG, image)
        await self.gateway.send(MessageType.VIDEO_MODE_ON)
        self.current_emotion = normalized
        return normalized

    async def hide(self) -> None:
        await self.gateway.send(MessageType.VIDEO_MODE_OFF)
        self.current_emotion = None
