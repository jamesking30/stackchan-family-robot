def test_codex_task_becomes_robot_display_state(client):
    response = client.post(
        "/v1/tasks/report",
        json={
            "task_id": "codex-build-1",
            "source": "codex",
            "title": "正在编译机器人固件",
            "status": "running",
            "progress": 0.42,
            "summary": "检查依赖与硬件配置",
        },
    )
    assert response.status_code == 200
    state = client.get("/v1/display/state").json()
    assert state["mode"] == "task"
    assert state["source"] == "codex"
    assert state["progress"] == 0.42
    assert state["emotion"] == "thinking"
