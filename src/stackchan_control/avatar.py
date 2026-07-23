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
        if not image.startswith(b"\xff\xd8"):
            raise AvatarAssetError(f"avatar asset is not a JPEG: {path}")
        # Load the frame while the overlay is hidden, then reveal it. This avoids
        # displaying an old expression while the new JPEG is being decoded.
        await self.gateway.send(MessageType.JPEG, image)
        await self.gateway.send(MessageType.VIDEO_MODE_ON)
        self.current_emotion = normalized
        return normalized

    async def hide(self) -> None:
        await self.gateway.send(MessageType.VIDEO_MODE_OFF)
        self.current_emotion = None
