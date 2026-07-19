from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP


mcp = FastMCP("stackchan-family-admin")
CONTROL_URL = os.getenv("ROBOT_CONTROL_URL", "http://127.0.0.1:8765").rstrip("/")
ADMIN_KEY = os.getenv("ROBOT_ADMIN_API_KEY")


async def api(method: str, path: str, payload: dict[str, Any] | None = None):
    headers = {"X-Robot-Admin-Key": ADMIN_KEY} if ADMIN_KEY else {}
    async with httpx.AsyncClient(base_url=CONTROL_URL, headers=headers, timeout=15) as client:
        response = await client.request(method, path, json=payload)
        response.raise_for_status()
        return response.json() if response.content else None


@mcp.tool()
async def get_robot_character() -> dict[str, Any]:
    """Read the active robot character documents and version before proposing a change."""
    return await api("GET", "/v1/characters/family-companion")


@mcp.tool()
async def update_answer_style(instructions: str, reason: str) -> dict[str, Any]:
    """Create and activate a reversible answer-style version. Read the current character first."""
    active = await get_robot_character()
    return await api("POST", "/v1/characters/family-companion/versions", {
        "base_version": active["version"],
        "patch": {"response_instructions": instructions},
        "reason": reason,
        "actor": "mcp",
        "activate": True,
    })


@mcp.tool()
async def rollback_robot_character(target_version: str | None = None) -> dict[str, Any]:
    """Roll back the robot character to its parent or to a specified known version."""
    return await api("POST", "/v1/characters/family-companion/rollback", {
        "target_version": target_version,
        "actor": "mcp",
        "reason": "rollback requested through MCP",
    })


@mcp.tool()
async def list_family_users() -> list[dict[str, Any]]:
    """List the four local family profiles and their role assignment."""
    return await api("GET", "/v1/users")


@mcp.tool()
async def search_user_memory(user_id: str, query: str = "") -> list[dict[str, Any]]:
    """Search only one user's memories. Never use one user's memories for another user."""
    suffix = f"?query={quote(query)}" if query else ""
    return await api("GET", f"/v1/users/{user_id}/memories{suffix}")


@mcp.tool()
async def remember_for_user(
    user_id: str,
    content: str,
    namespace: str = "note",
    source: str = "user_confirmed",
) -> dict[str, Any]:
    """Save a memory for one user. Child AI-inferred memories automatically require review."""
    return await api("POST", f"/v1/users/{user_id}/memories", {
        "namespace": namespace,
        "content": content,
        "source": source,
        "sensitivity": "normal",
        "importance": 0.5,
    })


@mcp.tool()
async def forget_user_memory(user_id: str, memory_id: str) -> dict[str, bool]:
    """Forget one specific memory belonging to one specific user."""
    await api("DELETE", f"/v1/users/{user_id}/memories/{memory_id}")
    return {"forgotten": True}


@mcp.tool()
async def approve_child_memory(user_id: str, memory_id: str) -> dict[str, Any]:
    """Approve one pending AI-inferred child memory after an adult has reviewed it."""
    return await api("POST", f"/v1/users/{user_id}/memories/{memory_id}/approve")


@mcp.tool()
async def report_agent_task(
    task_id: str,
    source: str,
    title: str,
    status: str,
    progress: float = 0,
    summary: str = "",
) -> dict[str, Any]:
    """Report Codex/OpenClaw task progress so the robot can show it on its face."""
    return await api("POST", "/v1/tasks/report", {
        "task_id": task_id,
        "source": source,
        "title": title,
        "status": status,
        "progress": progress,
        "summary": summary,
    })


@mcp.tool()
async def list_agent_tasks() -> list[dict[str, Any]]:
    """List recent Codex and OpenClaw task states."""
    return await api("GET", "/v1/tasks")


def run() -> None:
    mcp.run()


if __name__ == "__main__":
    run()
