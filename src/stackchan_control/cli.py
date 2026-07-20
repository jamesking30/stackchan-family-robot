from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = os.getenv("ROBOT_CONTROL_URL", "http://127.0.0.1:8765").rstrip("/")
ADMIN_KEY = os.getenv("ROBOT_ADMIN_API_KEY")


def request(
    method: str,
    path: str,
    payload: dict | None = None,
    *,
    timeout: float = 10,
):
    headers = {"Content-Type": "application/json"}
    if ADMIN_KEY:
        headers["X-Robot-Admin-Key"] = ADMIN_KEY
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 204:
                return None
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise SystemExit(f"API error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"无法连接 {BASE_URL}：{exc.reason}") from exc


def print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="robotctl", description="StackChan local control CLI")
    commands = parser.add_subparsers(dest="command", required=True)

    character = commands.add_parser("character")
    character_commands = character.add_subparsers(dest="character_command", required=True)
    character_commands.add_parser("show")
    style = character_commands.add_parser("style")
    style.add_argument("instructions")
    style.add_argument("--reason", default="updated through robotctl")
    rollback = character_commands.add_parser("rollback")
    rollback.add_argument("--version")

    users = commands.add_parser("users")
    users.add_subparsers(dest="users_command", required=True).add_parser("list")

    memories = commands.add_parser("memories")
    memory_commands = memories.add_subparsers(dest="memory_command", required=True)
    memory_list = memory_commands.add_parser("list")
    memory_list.add_argument("user_id")
    memory_add = memory_commands.add_parser("add")
    memory_add.add_argument("user_id")
    memory_add.add_argument("content")
    memory_add.add_argument("--namespace", default="note")
    memory_add.add_argument("--source", default="user_confirmed")
    memory_forget = memory_commands.add_parser("forget")
    memory_forget.add_argument("user_id")
    memory_forget.add_argument("memory_id")

    tasks = commands.add_parser("tasks")
    task_commands = tasks.add_subparsers(dest="task_command", required=True)
    task_commands.add_parser("list")
    report = task_commands.add_parser("report")
    report.add_argument("--id", dest="task_id", default="manual-task")
    report.add_argument("--source", choices=["codex", "openclaw", "system"], required=True)
    report.add_argument("--title", required=True)
    report.add_argument("--status", choices=["queued", "running", "waiting", "completed", "failed", "cancelled"], required=True)
    report.add_argument("--progress", type=float, default=0)
    report.add_argument("--summary", default="")

    voice = commands.add_parser("voice")
    voice_commands = voice.add_subparsers(dest="voice_command", required=True)
    voice_commands.add_parser("state")
    voice_start = voice_commands.add_parser("start")
    voice_start.add_argument("--user", default="user-2")
    voice_commands.add_parser("stop")
    voice_commands.add_parser("interrupt")
    voice_turn = voice_commands.add_parser("say")
    voice_turn.add_argument("transcript")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "character" and args.character_command == "show":
        print_json(request("GET", "/v1/characters/family-companion"))
    elif args.command == "character" and args.character_command == "style":
        active = request("GET", "/v1/characters/family-companion")
        print_json(request("POST", "/v1/characters/family-companion/versions", {
            "base_version": active["version"],
            "patch": {"response_instructions": args.instructions},
            "reason": args.reason,
            "actor": "robotctl",
            "activate": True,
        }))
    elif args.command == "character" and args.character_command == "rollback":
        print_json(request("POST", "/v1/characters/family-companion/rollback", {
            "target_version": args.version,
            "actor": "robotctl",
            "reason": "rollback through robotctl",
        }))
    elif args.command == "users":
        print_json(request("GET", "/v1/users"))
    elif args.command == "memories" and args.memory_command == "list":
        print_json(request("GET", f"/v1/users/{urllib.parse.quote(args.user_id)}/memories"))
    elif args.command == "memories" and args.memory_command == "add":
        print_json(request("POST", f"/v1/users/{urllib.parse.quote(args.user_id)}/memories", {
            "namespace": args.namespace,
            "content": args.content,
            "source": args.source,
            "sensitivity": "normal",
            "importance": 0.5,
        }))
    elif args.command == "memories" and args.memory_command == "forget":
        request("DELETE", f"/v1/users/{urllib.parse.quote(args.user_id)}/memories/{urllib.parse.quote(args.memory_id)}")
        print("已遗忘")
    elif args.command == "tasks" and args.task_command == "list":
        print_json(request("GET", "/v1/tasks"))
    elif args.command == "tasks" and args.task_command == "report":
        print_json(request("POST", "/v1/tasks/report", {
            "task_id": args.task_id,
            "source": args.source,
            "title": args.title,
            "status": args.status,
            "progress": args.progress,
            "summary": args.summary,
        }))
    elif args.command == "voice" and args.voice_command == "state":
        print_json(request("GET", "/v1/voice/state"))
    elif args.command == "voice" and args.voice_command == "start":
        print_json(request("POST", "/v1/voice/start", {"user_id": args.user}))
    elif args.command == "voice" and args.voice_command == "stop":
        print_json(request("POST", "/v1/voice/stop"))
    elif args.command == "voice" and args.voice_command == "interrupt":
        print_json(request("POST", "/v1/voice/interrupt"))
    elif args.command == "voice" and args.voice_command == "say":
        print_json(
            request(
                "POST",
                "/v1/voice/turn",
                {"transcript": args.transcript},
                timeout=120,
            )
        )
    else:
        sys.exit(2)


if __name__ == "__main__":
    main()
