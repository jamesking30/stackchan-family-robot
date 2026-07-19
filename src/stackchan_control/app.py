from __future__ import annotations

from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from .repository import ConflictError, NotFoundError, RepositoryError, RobotRepository
from .schemas import (
    CharacterVersion,
    CharacterVersionCreate,
    DisplayState,
    MemoryCreate,
    MemoryItem,
    PromptPreview,
    RollbackRequest,
    TaskItem,
    TaskReport,
    UserProfile,
    UserUpdate,
)
from .settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    current_settings = settings or Settings.from_env()
    repository = RobotRepository(
        current_settings.db_path, current_settings.seed_character_dir
    )

    app = FastAPI(
        title="StackChan Family Robot Control API",
        version="0.1.0",
        description="Local-first character, memory, task and display control plane.",
    )
    app.state.settings = current_settings
    app.state.repository = repository

    def repo(request: Request) -> RobotRepository:
        return request.app.state.repository

    def require_admin(
        request: Request,
        x_robot_admin_key: Annotated[str | None, Header()] = None,
    ) -> None:
        expected = request.app.state.settings.admin_api_key
        if expected and x_robot_admin_key != expected:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin key")

    @app.exception_handler(NotFoundError)
    async def not_found_handler(_: Request, exc: NotFoundError):
        return _json_error(404, str(exc))

    @app.exception_handler(ConflictError)
    async def conflict_handler(_: Request, exc: ConflictError):
        return _json_error(409, str(exc))

    @app.exception_handler(RepositoryError)
    async def repository_handler(_: Request, exc: RepositoryError):
        return _json_error(422, str(exc))

    @app.get("/", include_in_schema=False)
    def admin_page() -> FileResponse:
        return FileResponse(current_settings.web_dir / "index.html")

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "ok": True,
            "service": "stackchan-control",
            "version": "0.1.0",
            "local_first": True,
        }

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
    def report_task(
        body: TaskReport, repository: RobotRepository = Depends(repo)
    ):
        return repository.report_task(body.model_dump())

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
