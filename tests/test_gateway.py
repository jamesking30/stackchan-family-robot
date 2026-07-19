from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from stackchan_control.gateway import (
    MessageType,
    ProtocolError,
    pack_frame,
    unpack_frame,
)
from stackchan_control.app import create_app
from stackchan_control.settings import PROJECT_ROOT, Settings


ADMIN_HEADERS = {"X-Robot-Admin-Key": "admin-secret"}
DEVICE_HEADERS = {"Authorization": "device-secret"}
WS_PATH = "/stackChan/ws?deviceType=StackChan"


def decode_json_frame(data: bytes) -> tuple[int, dict[str, object]]:
    frame = unpack_frame(data)
    return frame.message_type, json.loads(frame.payload)


def test_binary_frame_round_trip_and_validation():
    packet = pack_frame(MessageType.TEXT_MESSAGE, "你好".encode())
    frame = unpack_frame(packet)
    assert frame.message_type == MessageType.TEXT_MESSAGE
    assert frame.payload.decode() == "你好"

    with pytest.raises(ProtocolError):
        unpack_frame(b"\x07\x00")
    with pytest.raises(ProtocolError):
        unpack_frame(b"\x07\x00\x00\x00\x02x")


def test_websocket_requires_device_key(gateway_client):
    with pytest.raises(WebSocketDisconnect) as caught:
        with gateway_client.websocket_connect(WS_PATH):
            pass
    assert caught.value.code == 1008


def test_lan_listener_requires_admin_key(tmp_path: Path):
    settings = Settings(
        db_path=tmp_path / "unsafe.db",
        seed_character_dir=PROJECT_ROOT / "config" / "seed_character",
        web_dir=PROJECT_ROOT / "web",
        host="0.0.0.0",
        device_api_key="device-only",
    )
    with pytest.raises(RuntimeError, match="ROBOT_ADMIN_API_KEY"):
        create_app(settings)


def test_gateway_controls_connected_stackchan(gateway_client):
    with gateway_client.websocket_connect(WS_PATH, headers=DEVICE_HEADERS) as websocket:
        websocket.send_text('{"type":"hello","msg":"Hello from StackChan!"}')

        state = gateway_client.get(
            "/v1/device/state", headers=ADMIN_HEADERS
        ).json()
        assert state["online"] is True
        assert state["frames_received"] == 1

        response = gateway_client.post(
            "/v1/device/motion",
            headers=ADMIN_HEADERS,
            json={"yaw_degrees": -12.5, "pitch_degrees": 20, "speed": 180},
        )
        assert response.status_code == 200
        message_type, payload = decode_json_frame(websocket.receive_bytes())
        assert message_type == MessageType.CONTROL_MOTION
        assert payload == {
            "yawServo": {"angle": -125, "speed": 180},
            "pitchServo": {"angle": 200, "speed": 180},
        }

        response = gateway_client.post(
            "/v1/device/expression",
            headers=ADMIN_HEADERS,
            json={"emotion": "happy", "mouth_weight": 45},
        )
        assert response.status_code == 200
        message_type, payload = decode_json_frame(websocket.receive_bytes())
        assert message_type == MessageType.CONTROL_AVATAR
        assert payload["leftEye"] == {"weight": 72, "rotation": 1550}
        assert payload["rightEye"] == {"weight": 72, "rotation": -1550}
        assert payload["mouth"] == {"weight": 45}

        response = gateway_client.post(
            "/v1/device/text",
            headers=ADMIN_HEADERS,
            json={"name": "小助手", "content": "任务完成了"},
        )
        assert response.status_code == 200
        message_type, payload = decode_json_frame(websocket.receive_bytes())
        assert message_type == MessageType.TEXT_MESSAGE
        assert payload == {"name": "小助手", "content": "任务完成了"}

    state = gateway_client.get("/v1/device/state", headers=ADMIN_HEADERS).json()
    assert state["online"] is False


def test_gateway_enforces_motion_limits_and_admin_key(gateway_client):
    assert gateway_client.get("/v1/device/state").status_code == 401
    response = gateway_client.post(
        "/v1/device/motion",
        headers=ADMIN_HEADERS,
        json={"yaw_degrees": 90, "pitch_degrees": 20, "speed": 180},
    )
    assert response.status_code == 422


def test_codex_task_status_is_forwarded_to_robot(gateway_client):
    with gateway_client.websocket_connect(WS_PATH, headers=DEVICE_HEADERS) as websocket:
        response = gateway_client.post(
            "/v1/tasks/report",
            headers=ADMIN_HEADERS,
            json={
                "task_id": "codex-build-1",
                "source": "codex",
                "title": "编译产品固件",
                "status": "running",
                "progress": 0.6,
                "summary": "正在执行离线校验",
                "display_emotion": "task_running",
            },
        )
        assert response.status_code == 200

        expression_type, expression = decode_json_frame(websocket.receive_bytes())
        text_type, text_payload = decode_json_frame(websocket.receive_bytes())
        assert expression_type == MessageType.CONTROL_AVATAR
        assert expression["leftEye"] == {"weight": 75, "rotation": 0}
        assert text_type == MessageType.TEXT_MESSAGE
        assert text_payload["name"] == "codex"
        assert "编译产品固件" in text_payload["content"]


def test_gateway_heartbeat_and_device_rest_compatibility(gateway_client):
    assert gateway_client.get("/stackChan/device/user").status_code == 401
    assert gateway_client.get(
        "/stackChan/device/user", headers=DEVICE_HEADERS
    ).json() == {"code": 0, "data": {"username": "Local Family"}}
    assert gateway_client.get(
        "/stackChan/device/info", headers=DEVICE_HEADERS
    ).json() == {"code": 0, "data": {"name": "StackChan Family"}}
    assert gateway_client.get("/stackChan/apps").json() == {"code": 0, "data": []}
    assert gateway_client.post("/v1/device/ota/check").json() == {
        "firmware": {"version": "1.4.3", "url": ""}
    }

    with gateway_client.websocket_connect(WS_PATH, headers=DEVICE_HEADERS) as websocket:
        ping = unpack_frame(websocket.receive_bytes())
        assert ping.message_type == MessageType.HEARTBEAT_PING
        websocket.send_bytes(pack_frame(MessageType.HEARTBEAT_PONG))
        state = gateway_client.get(
            "/v1/device/state", headers=ADMIN_HEADERS
        ).json()
        assert state["last_message_type"] == MessageType.HEARTBEAT_PONG


def test_gateway_sends_heartbeat_during_continuous_audio(gateway_client):
    with gateway_client.websocket_connect(WS_PATH, headers=DEVICE_HEADERS) as websocket:
        for _ in range(5):
            websocket.send_bytes(pack_frame(MessageType.OPUS, b"audio"))
            time.sleep(0.012)
        ping = unpack_frame(websocket.receive_bytes())
        assert ping.message_type == MessageType.HEARTBEAT_PING
