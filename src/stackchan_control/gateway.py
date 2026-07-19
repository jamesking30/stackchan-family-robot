from __future__ import annotations

import asyncio
import json
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any

from fastapi import WebSocket


MAX_FRAME_PAYLOAD = 8 * 1024 * 1024


class MessageType(IntEnum):
    OPUS = 0x01
    JPEG = 0x02
    CONTROL_AVATAR = 0x03
    CONTROL_MOTION = 0x04
    START_CAMERA_STREAM = 0x05
    STOP_CAMERA_STREAM = 0x06
    TEXT_MESSAGE = 0x07
    REQUEST_CALL = 0x09
    DECLINE_CALL = 0x0A
    ACCEPT_CALL = 0x0B
    END_CALL = 0x0C
    SET_DEVICE_NAME = 0x0D
    GET_DEVICE_NAME = 0x0E
    HEARTBEAT_PING = 0x10
    HEARTBEAT_PONG = 0x11
    VIDEO_MODE_ON = 0x12
    VIDEO_MODE_OFF = 0x13
    DANCE_SEQUENCE = 0x14
    START_AUDIO_STREAM = 0x18
    STOP_AUDIO_STREAM = 0x19


class ProtocolError(ValueError):
    pass


class DeviceOfflineError(RuntimeError):
    pass


@dataclass(frozen=True)
class Frame:
    message_type: int
    payload: bytes


def pack_frame(message_type: int | MessageType, payload: bytes = b"") -> bytes:
    if not 0 <= int(message_type) <= 0xFF:
        raise ProtocolError("message type must fit in one byte")
    if len(payload) > MAX_FRAME_PAYLOAD:
        raise ProtocolError("frame payload is too large")
    return bytes((int(message_type),)) + struct.pack(">I", len(payload)) + payload


def unpack_frame(data: bytes) -> Frame:
    if len(data) < 5:
        raise ProtocolError("binary frame is shorter than its five-byte header")
    payload_length = struct.unpack(">I", data[1:5])[0]
    if payload_length > MAX_FRAME_PAYLOAD:
        raise ProtocolError("declared frame payload is too large")
    if len(data) != payload_length + 5:
        raise ProtocolError("binary frame length does not match its header")
    return Frame(data[0], data[5:])


@dataclass
class DeviceSession:
    device_id: str
    websocket: WebSocket
    connected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_pong_monotonic: float = field(default_factory=time.monotonic)
    frames_received: int = 0
    frames_sent: int = 0
    last_message_type: int | None = None
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class StackChanGateway:
    """Transient StackChan connection state; audio and camera payloads are never stored."""

    def __init__(self, default_device_id: str) -> None:
        self.default_device_id = default_device_id
        self._sessions: dict[str, DeviceSession] = {}
        self._lock = asyncio.Lock()

    async def register(self, device_id: str, websocket: WebSocket) -> DeviceSession:
        session = DeviceSession(device_id=device_id, websocket=websocket)
        async with self._lock:
            previous = self._sessions.get(device_id)
            self._sessions[device_id] = session
        if previous is not None:
            try:
                await previous.websocket.close(code=1012, reason="replaced by new connection")
            except RuntimeError:
                pass
        return session

    async def disconnect(self, session: DeviceSession) -> None:
        async with self._lock:
            if self._sessions.get(session.device_id) is session:
                self._sessions.pop(session.device_id, None)

    async def record_text(self, session: DeviceSession) -> None:
        session.last_seen = datetime.now(timezone.utc)
        session.frames_received += 1
        session.last_message_type = None

    async def record_frame(self, session: DeviceSession, frame: Frame) -> None:
        session.last_seen = datetime.now(timezone.utc)
        session.frames_received += 1
        session.last_message_type = frame.message_type
        if frame.message_type == MessageType.HEARTBEAT_PONG:
            session.last_pong_monotonic = time.monotonic()

    async def send(
        self,
        message_type: int | MessageType,
        payload: bytes = b"",
        device_id: str | None = None,
    ) -> None:
        target_id = device_id or self.default_device_id
        async with self._lock:
            session = self._sessions.get(target_id)
        if session is None:
            raise DeviceOfflineError(f"device {target_id} is offline")

        packet = pack_frame(message_type, payload)
        try:
            async with session.send_lock:
                await session.websocket.send_bytes(packet)
            session.frames_sent += 1
        except RuntimeError as exc:
            await self.disconnect(session)
            raise DeviceOfflineError(f"device {target_id} disconnected") from exc

    async def send_json(
        self,
        message_type: int | MessageType,
        payload: dict[str, Any],
        device_id: str | None = None,
    ) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        await self.send(message_type, data, device_id)

    async def snapshot(self, device_id: str | None = None) -> dict[str, Any]:
        target_id = device_id or self.default_device_id
        async with self._lock:
            session = self._sessions.get(target_id)
        if session is None:
            return {
                "device_id": target_id,
                "online": False,
                "connected_at": None,
                "last_seen": None,
                "frames_received": 0,
                "frames_sent": 0,
                "last_message_type": None,
            }
        return {
            "device_id": target_id,
            "online": True,
            "connected_at": session.connected_at,
            "last_seen": session.last_seen,
            "frames_received": session.frames_received,
            "frames_sent": session.frames_sent,
            "last_message_type": session.last_message_type,
        }
