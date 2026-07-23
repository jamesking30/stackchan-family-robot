from __future__ import annotations

import asyncio
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

ANIMATION_FILES = {
    "speaking_closed": "neutral.jpg",
    "speaking_half": "speaking-half.jpg",
    "speaking_open": "speaking-open.jpg",
    "transition_half_blink": "transition-half-blink.jpg",
    "transition_blink": "transition-blink.jpg",
    "idle_look_left": "idle-look-left.jpg",
    "idle_look_right": "idle-look-right.jpg",
    "idle_hair_touch_1": "idle-hair-touch-1.jpg",
    "idle_hair_touch_2": "idle-hair-touch-2.jpg",
}

IDLE_GESTURES = {
    "blink": (
        ("transition_half_blink", 0.045),
        ("transition_blink", 0.070),
        ("transition_half_blink", 0.045),
        ("speaking_closed", 0.0),
    ),
    "look_left": (
        ("transition_half_blink", 0.045),
        ("transition_blink", 0.060),
        ("idle_look_left", 0.500),
        ("transition_half_blink", 0.045),
        ("speaking_closed", 0.0),
    ),
    "look_right": (
        ("transition_half_blink", 0.045),
        ("transition_blink", 0.060),
        ("idle_look_right", 0.500),
        ("transition_half_blink", 0.045),
        ("speaking_closed", 0.0),
    ),
    "hair_touch": (
        ("idle_hair_touch_1", 0.120),
        ("idle_hair_touch_2", 0.280),
        ("idle_hair_touch_1", 0.120),
        ("speaking_closed", 0.0),
    ),
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
        self._frame_lock = asyncio.Lock()
        self._image_cache: dict[str, bytes] = {}

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

    def _load_image(self, filename: str) -> bytes:
        cached = self._image_cache.get(filename)
        if cached is not None:
            return cached
        path = self.assets_dir / filename
        if not path.is_file():
            raise AvatarAssetError(f"avatar asset is missing: {path}")
        image = path.read_bytes()
        try:
            validate_esp_avatar(image)
        except AvatarAssetError as exc:
            raise AvatarAssetError(f"{path}: {exc}") from exc
        self._image_cache[filename] = image
        return image

    async def _send_frame(self, filename: str) -> None:
        image = self._load_image(filename)
        await self.gateway.send(MessageType.JPEG, image)
        await self.gateway.send(MessageType.VIDEO_MODE_ON)

    async def show(self, emotion: str) -> str:
        normalized, path = self.resolve(emotion)
        async with self._frame_lock:
            if self.current_emotion is not None and self.current_emotion != normalized:
                for frame in (
                    "transition_half_blink",
                    "transition_blink",
                    "transition_half_blink",
                ):
                    await self._send_frame(ANIMATION_FILES[frame])
                    await asyncio.sleep(0.04)
            await self._send_frame(path.name)
            self.current_emotion = normalized
        return normalized

    async def show_speaking_frame(self, level: int) -> None:
        frame = {
            0: "speaking_closed",
            1: "speaking_half",
            2: "speaking_open",
        }.get(level, "speaking_half")
        async with self._frame_lock:
            await self._send_frame(ANIMATION_FILES[frame])
            self.current_emotion = "speaking"

    async def play_idle_gesture(self, gesture: str) -> bool:
        sequence = IDLE_GESTURES.get(gesture)
        if sequence is None:
            raise ValueError(f"unknown idle gesture: {gesture}")
        async with self._frame_lock:
            if self.current_emotion != "neutral":
                return False
            for frame, duration in sequence:
                await self._send_frame(ANIMATION_FILES[frame])
                if duration:
                    await asyncio.sleep(duration)
            self.current_emotion = "neutral"
        return True

    async def hide(self) -> None:
        async with self._frame_lock:
            await self.gateway.send(MessageType.VIDEO_MODE_OFF)
            self.current_emotion = None
