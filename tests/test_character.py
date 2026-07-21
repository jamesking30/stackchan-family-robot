def test_default_character_identity_is_aili(client):
    active = client.get("/v1/characters/family-companion").json()
    preview = client.get("/v1/characters/family-companion/prompt").json()

    assert "name: 爱莉 / Aili" in active["documents"]["manifest"]
    assert "你是家庭机器人“爱莉（Aili）”" in preview["system_prompt"]
    assert "zh-CN: 爱莉" in preview["system_prompt"]
    assert "en-US: Aili" in preview["system_prompt"]


def test_character_change_is_versioned_and_reversible(client):
    original = client.get("/v1/characters/family-companion").json()
    response = client.post(
        "/v1/characters/family-companion/versions",
        json={
            "base_version": original["version"],
            "patch": {"response_instructions": "回答只使用一句话。"},
            "reason": "test concise style",
            "actor": "pytest",
            "activate": True,
        },
    )
    assert response.status_code == 201
    changed = response.json()
    assert changed["version"] != original["version"]
    assert changed["documents"]["response_instructions"] == "回答只使用一句话。"

    preview = client.get("/v1/characters/family-companion/prompt").json()
    assert preview["version"] == changed["version"]
    assert "回答只使用一句话" in preview["system_prompt"]
    assert "家庭与儿童安全" in preview["system_prompt"]

    rolled_back = client.post(
        "/v1/characters/family-companion/rollback",
        json={"actor": "pytest", "reason": "verify rollback"},
    )
    assert rolled_back.status_code == 200
    assert rolled_back.json()["version"] == original["version"]


def test_stale_character_update_is_rejected(client):
    original = client.get("/v1/characters/family-companion").json()
    payload = {
        "base_version": original["version"],
        "patch": {"response_instructions": "第一版"},
        "reason": "first update",
        "actor": "pytest",
    }
    assert client.post("/v1/characters/family-companion/versions", json=payload).status_code == 201
    payload["patch"] = {"response_instructions": "冲突版本"}
    assert client.post("/v1/characters/family-companion/versions", json=payload).status_code == 409


def test_admin_key_protects_writes(protected_client):
    active = protected_client.get("/v1/characters/family-companion").json()
    payload = {
        "base_version": active["version"],
        "patch": {"response_instructions": "受保护的修改"},
        "reason": "auth test",
        "actor": "pytest",
    }
    assert protected_client.post("/v1/characters/family-companion/versions", json=payload).status_code == 401
    assert protected_client.post(
        "/v1/characters/family-companion/versions",
        json=payload,
        headers={"X-Robot-Admin-Key": "test-secret"},
    ).status_code == 201
