from __future__ import annotations

import asyncio
import secrets
import time
from typing import Annotated

import uvicorn
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse

from .gateway import (
    DeviceOfflineError,
    MessageType,
    ProtocolError,
    StackChanGateway,
    unpack_frame,
)
from .repository import ConflictError, NotFoundError, RepositoryError, RobotRepository
from .schemas import (
    CharacterVersion,
    CharacterVersionCreate,
    DeviceState,
    DisplayState,
    MemoryCreate,
    MemoryItem,
    PromptPreview,
    RobotExpressionCommand,
    RobotMotionCommand,
    RobotTextCommand,
    RollbackRequest,
    TaskItem,
    TaskReport,
    UserProfile,
    UserUpdate,
    VoiceStartRequest,
    VoiceStateResponse,
    VoiceTextTurn,
)
from .settings import Settings
from .voice import (
    OpusCodec,
    VoiceError,
    VoiceMode,
    VoiceProvider,
    VoiceSessionManager,
)
from .wake import WakeWordDetector


EXPRESSION_PROFILES = {
    "neutral": {"weight": 100, "left_rotation": 0, "right_rotation": 0},
    "happy": {"weight": 72, "left_rotation": 1550, "right_rotation": -1550},
    "angry": {"weight": 70, "left_rotation": 450, "right_rotation": -450},
    "sad": {"weight": 70, "left_rotation": -400, "right_rotation": 400},
    "doubt": {"weight": 75, "left_rotation": 0, "right_rotation": 0},
    "sleepy": {"weight": 35, "left_rotation": -50, "right_rotation": 50},
}
DISPLAY_EMOTIONS = {
    "neutral": "neutral",
    "happy": "happy",
    "excited": "happy",
    "thinking": "doubt",
    "focused": "doubt",
    "concerned": "sad",
    "apologetic": "sad",
    "task_running": "doubt",
    "task_complete": "happy",
    "task_failed": "sad",
}


def expression_payload(emotion: str, mouth_weight: int | None = None) -> dict[str, object]:
    profile = EXPRESSION_PROFILES[emotion]
    payload: dict[str, object] = {
        "leftEye": {
            "weight": profile["weight"],
            "rotation": profile["left_rotation"],
        },
        "rightEye": {
            "weight": profile["weight"],
            "rotation": profile["right_rotation"],
        },
    }
    if mouth_weight is not None:
        payload["mouth"] = {"weight": mouth_weight}
    return payload


def create_app(
    settings: Settings | None = None,
    voice_provider: VoiceProvider | None = None,
    voice_codec: OpusCodec | None = None,
    wake_detector: WakeWordDetector | None = None,
) -> FastAPI:
    current_settings = settings or Settings.from_env()
    if current_settings.host not in {"127.0.0.1", "localhost", "::1"} and not (
        current_settings.admin_api_key
    ):
        raise RuntimeError("ROBOT_ADMIN_API_KEY is required when listening on the LAN")
    repository = RobotRepository(
        current_settings.db_path, current_settings.seed_character_dir
    )
    gateway = StackChanGateway(current_settings.device_id)
    voice = VoiceSessionManager(
        current_settings,
        repository,
        gateway,
        provider=voice_provider,
        codec=voice_codec,
        wake_detector=wake_detector,
    )

    app = FastAPI(
        title="StackChan Family Robot Control API",
        version="0.3.0",
        description="Local-first control plane, StackChan LAN gateway and bilingual voice loop.",
    )
    app.state.settings = current_settings
    app.state.repository = repository
    app.state.gateway = gateway
    app.state.voice = voice

    async def sync_display_to_device() -> None:
        display = repository.display_state()
        emotion = DISPLAY_EMOTIONS.get(str(display["emotion"]), "neutral")
        await gateway.send_json(
            MessageType.CONTROL_AVATAR,
            expression_payload(emotion),
        )
        content = str(display["title"])
        if display["subtitle"]:
            content = f"{content}：{display['subtitle']}"
        await gateway.send_json(
            MessageType.TEXT_MESSAGE,
            {"name": display["source"] or "任务状态", "content": content[:240]},
        )

    def repo(request: Request) -> RobotRepository:
        return request.app.state.repository

    def require_admin(
        request: Request,
        x_robot_admin_key: Annotated[str | None, Header()] = None,
    ) -> None:
        expected = request.app.state.settings.admin_api_key
        if expected and (
            not x_robot_admin_key
            or not secrets.compare_digest(x_robot_admin_key, expected)
        ):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin key")

    def require_device(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        expected = request.app.state.settings.device_api_key
        if not expected or not authorization or not secrets.compare_digest(
            authorization, expected
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid device key",
            )

    @app.exception_handler(NotFoundError)
    async def not_found_handler(_: Request, exc: NotFoundError):
        return _json_error(404, str(exc))

    @app.exception_handler(ConflictError)
    async def conflict_handler(_: Request, exc: ConflictError):
        return _json_error(409, str(exc))

    @app.exception_handler(RepositoryError)
    async def repository_handler(_: Request, exc: RepositoryError):
        return _json_error(422, str(exc))

    @app.exception_handler(DeviceOfflineError)
    async def device_offline_handler(_: Request, exc: DeviceOfflineError):
        return _json_error(409, str(exc))

    @app.exception_handler(VoiceError)
    async def voice_error_handler(_: Request, exc: VoiceError):
        return _json_error(422, str(exc))

    @app.get("/", include_in_schema=False)
    def admin_page() -> FileResponse:
        return FileResponse(current_settings.web_dir / "index.html")

    @app.get("/health")
    def health() -> dict[str, object]:
        voice_ready = voice.state.enabled and voice.state.mode != VoiceMode.ERROR
        return {
            "ok": True,
            "service": "stackchan-control",
            "version": "0.3.0",
            "local_first": True,
            "device_gateway_auth_configured": bool(current_settings.device_api_key),
            "voice_configured": bool(
                current_settings.deepseek_api_key
                and current_settings.voice_whisper_model.is_file()
            ),
            "voice_ready": voice_ready,
            "wake_word_ready": bool(
                voice_ready
                and (
                    not current_settings.voice_kws_enabled
                    or voice.wake_detector is not None
                )
            ),
        }

    @app.websocket("/stackChan/ws")
    async def stackchan_websocket(websocket: WebSocket) -> None:
        expected = current_settings.device_api_key
        provided = websocket.headers.get("authorization")
        if not expected or not provided or not secrets.compare_digest(provided, expected):
            await websocket.close(code=1008, reason="invalid device key")
            return

        device_type = websocket.query_params.get("deviceType")
        if device_type != "StackChan":
            await websocket.close(code=1008, reason="invalid device type")
            return

        device_id = websocket.query_params.get("deviceId", current_settings.device_id)
        if device_id != current_settings.device_id:
            await websocket.close(code=1008, reason="unknown device")
            return

        await websocket.accept()
        session = await gateway.register(device_id, websocket)
        await voice.on_device_connected()
        last_ping_monotonic = time.monotonic()
        try:
            while True:
                elapsed = time.monotonic() - last_ping_monotonic
                receive_timeout = max(
                    0.01, current_settings.gateway_heartbeat_seconds - elapsed
                )
                try:
                    message = await asyncio.wait_for(
                        websocket.receive(),
                        timeout=receive_timeout,
                    )
                except asyncio.TimeoutError:
                    message = None

                if (
                    time.monotonic() - session.last_pong_monotonic
                    > current_settings.gateway_timeout_seconds
                ):
                    await websocket.close(code=1001, reason="heartbeat timeout")
                    break

                if (
                    time.monotonic() - last_ping_monotonic
                    >= current_settings.gateway_heartbeat_seconds
                ):
                    await gateway.send(MessageType.HEARTBEAT_PING, device_id=device_id)
                    last_ping_monotonic = time.monotonic()

                if message is None:
                    continue

                if message["type"] == "websocket.disconnect":
                    break
                binary = message.get("bytes")
                if binary is not None:
                    try:
                        frame = unpack_frame(binary)
                    except ProtocolError:
                        await websocket.close(code=1003, reason="invalid binary frame")
                        break
                    await gateway.record_frame(session, frame)
                    if frame.message_type == MessageType.OPUS:
                        await voice.ingest_opus(frame.payload)
                    elif frame.message_type == MessageType.VOICE_ACTIVITY:
                        await voice.voice_activity(bool(frame.payload[:1] == b"\x01"))
                elif message.get("text") is not None:
                    await gateway.record_text(session)
        except (WebSocketDisconnect, DeviceOfflineError, asyncio.CancelledError):
            pass
        finally:
            await gateway.disconnect(session)
            await voice.on_device_disconnected()

    @app.get(
        "/stackChan/device/user",
        dependencies=[Depends(require_device)],
    )
    def stackchan_user() -> dict[str, object]:
        return {"code": 0, "data": {"username": "Local Family"}}

    @app.get(
        "/stackChan/device/info",
        dependencies=[Depends(require_device)],
    )
    def stackchan_info() -> dict[str, object]:
        return {"code": 0, "data": {"name": "StackChan Family"}}

    @app.post(
        "/stackChan/device/unbind",
        dependencies=[Depends(require_device)],
    )
    def stackchan_unbind() -> dict[str, object]:
        return {"code": 0, "data": None}

    @app.get("/stackChan/apps")
    def stackchan_apps() -> dict[str, object]:
        return {"code": 0, "data": []}

    @app.api_route("/v1/device/ota/check", methods=["GET", "POST"])
    def ota_check() -> dict[str, object]:
        return {"firmware": {"version": "1.4.3", "url": ""}}

    @app.get(
        "/v1/device/state",
        response_model=DeviceState,
        dependencies=[Depends(require_admin)],
    )
    async def device_state() -> dict[str, object]:
        return await gateway.snapshot()

    @app.post(
        "/v1/device/motion",
        response_model=DeviceState,
        dependencies=[Depends(require_admin)],
    )
    async def device_motion(body: RobotMotionCommand) -> dict[str, object]:
        await gateway.send_json(
            MessageType.CONTROL_MOTION,
            {
                "yawServo": {
                    "angle": round(body.yaw_degrees * 10),
                    "speed": body.speed,
                },
                "pitchServo": {
                    "angle": round(body.pitch_degrees * 10),
                    "speed": body.speed,
                },
            },
        )
        return await gateway.snapshot()

    @app.post(
        "/v1/device/expression",
        response_model=DeviceState,
        dependencies=[Depends(require_admin)],
    )
    async def device_expression(body: RobotExpressionCommand) -> dict[str, object]:
        await gateway.send_json(
            MessageType.CONTROL_AVATAR,
            expression_payload(body.emotion, body.mouth_weight),
        )
        return await gateway.snapshot()

    @app.post(
        "/v1/device/text",
        response_model=DeviceState,
        dependencies=[Depends(require_admin)],
    )
    async def device_text(body: RobotTextCommand) -> dict[str, object]:
        await gateway.send_json(
            MessageType.TEXT_MESSAGE,
            {"name": body.name, "content": body.content},
        )
        return await gateway.snapshot()

    @app.post(
        "/v1/device/display/sync",
        response_model=DeviceState,
        dependencies=[Depends(require_admin)],
    )
    async def sync_device_display() -> dict[str, object]:
        await sync_display_to_device()
        return await gateway.snapshot()

    @app.get(
        "/v1/voice/state",
        response_model=VoiceStateResponse,
        dependencies=[Depends(require_admin)],
    )
    async def voice_state() -> dict[str, object]:
        return voice.state.snapshot()

    @app.post(
        "/v1/voice/start",
        response_model=VoiceStateResponse,
        dependencies=[Depends(require_admin)],
    )
    async def voice_start(body: VoiceStartRequest) -> dict[str, object]:
        return await voice.start(body.user_id)

    @app.post(
        "/v1/voice/stop",
        response_model=VoiceStateResponse,
        dependencies=[Depends(require_admin)],
    )
    async def voice_stop() -> dict[str, object]:
        return await voice.stop()

    @app.post(
        "/v1/voice/interrupt",
        response_model=VoiceStateResponse,
        dependencies=[Depends(require_admin)],
    )
    async def voice_interrupt() -> dict[str, object]:
        return await voice.interrupt()

    @app.post(
        "/v1/voice/turn",
        response_model=VoiceStateResponse,
        dependencies=[Depends(require_admin)],
    )
    async def voice_turn(body: VoiceTextTurn) -> dict[str, object]:
        return await voice.submit_text(body.transcript)

    @app.get(
        "/v1/users",
        response_model=list[UserProfile],
        dependencies=[Depends(require_admin)],
    )
    def list_users(repository: RobotRepository = Depends(repo)):
        return repository.list_users()

    @app.patch(
        "/v1/users/{user_id}",
        response_model=UserProfile,
        dependencies=[Depends(require_admin)],
    )
    def update_user(
        user_id: str,
        body: UserUpdate,
        repository: RobotRepository = Depends(repo),
    ):
        return repository.update_user(user_id, body.model_dump(exclude_none=True))

    @app.get("/v1/characters/{character_id}", response_model=CharacterVersion)
    def get_character(
        character_id: str, repository: RobotRepository = Depends(repo)
    ):
        return repository.get_character(character_id)

    @app.get("/v1/characters/{character_id}/prompt", response_model=PromptPreview)
    def preview_prompt(
        character_id: str, repository: RobotRepository = Depends(repo)
    ):
        return repository.prompt_preview(character_id)

    @app.post(
        "/v1/characters/{character_id}/versions",
        response_model=CharacterVersion,
        status_code=201,
        dependencies=[Depends(require_admin)],
    )
    def create_character_version(
        character_id: str,
        body: CharacterVersionCreate,
        repository: RobotRepository = Depends(repo),
    ):
        return repository.create_character_version(
            character_id,
            body.base_version,
            body.patch,
            body.reason,
            body.actor,
            body.activate,
        )

    @app.post(
        "/v1/characters/{character_id}/rollback",
        response_model=CharacterVersion,
        dependencies=[Depends(require_admin)],
    )
    def rollback_character(
        character_id: str,
        body: RollbackRequest,
        repository: RobotRepository = Depends(repo),
    ):
        return repository.rollback_character(
            character_id, body.target_version, body.actor, body.reason
        )

    @app.get(
        "/v1/users/{user_id}/memories",
        response_model=list[MemoryItem],
        dependencies=[Depends(require_admin)],
    )
    def list_memories(
        user_id: str,
        repository: RobotRepository = Depends(repo),
        query: str | None = Query(default=None, max_length=100),
        include_pending: bool = True,
    ):
        return repository.list_memories(user_id, query, include_pending)

    @app.post(
        "/v1/users/{user_id}/memories",
        response_model=MemoryItem,
        status_code=201,
        dependencies=[Depends(require_admin)],
    )
    def add_memory(
        user_id: str,
        body: MemoryCreate,
        repository: RobotRepository = Depends(repo),
    ):
        return repository.add_memory(
            user_id,
            body.namespace,
            body.content,
            body.source,
            body.sensitivity,
            body.importance,
        )

    @app.delete(
        "/v1/users/{user_id}/memories/{memory_id}",
        status_code=204,
        dependencies=[Depends(require_admin)],
    )
    def forget_memory(
        user_id: str,
        memory_id: str,
        repository: RobotRepository = Depends(repo),
    ) -> None:
        repository.forget_memory(user_id, memory_id, "admin")

    @app.post(
        "/v1/users/{user_id}/memories/{memory_id}/approve",
        response_model=MemoryItem,
        dependencies=[Depends(require_admin)],
    )
    def approve_memory(
        user_id: str,
        memory_id: str,
        repository: RobotRepository = Depends(repo),
    ):
        return repository.approve_memory(user_id, memory_id, "admin")

    @app.get(
        "/v1/tasks",
        response_model=list[TaskItem],
        dependencies=[Depends(require_admin)],
    )
    def list_tasks(
        repository: RobotRepository = Depends(repo),
        limit: int = Query(default=20, ge=1, le=100),
    ):
        return repository.list_tasks(limit)

    @app.post(
        "/v1/tasks/report",
        response_model=TaskItem,
        dependencies=[Depends(require_admin)],
    )
    async def report_task(
        body: TaskReport, repository: RobotRepository = Depends(repo)
    ):
        item = repository.report_task(body.model_dump())
        try:
            await sync_display_to_device()
        except DeviceOfflineError:
            pass
        return item

    @app.get("/v1/display/state", response_model=DisplayState)
    def display_state(repository: RobotRepository = Depends(repo)):
        return repository.display_state()

    return app


def _json_error(status_code: int, detail: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content={"detail": detail})


app = create_app()


def run() -> None:
    settings = Settings.from_env()
    uvicorn.run(
        "stackchan_control.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
