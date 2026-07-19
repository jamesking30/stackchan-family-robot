def test_four_user_slots_exist(client):
    users = client.get("/v1/users").json()
    assert [item["user_id"] for item in users] == ["user-1", "user-2", "user-3", "user-4"]


def test_memory_is_strictly_isolated_by_user(client):
    created = client.post(
        "/v1/users/user-1/memories",
        json={
            "namespace": "preference",
            "content": "喜欢蓝色",
            "source": "user_confirmed",
            "sensitivity": "normal",
            "importance": 0.8,
        },
    ).json()
    user_one = client.get("/v1/users/user-1/memories").json()
    user_two = client.get("/v1/users/user-2/memories").json()
    assert [item["memory_id"] for item in user_one] == [created["memory_id"]]
    assert user_two == []
    assert client.delete(f"/v1/users/user-2/memories/{created['memory_id']}").status_code == 404


def test_inferred_child_memory_requires_review(client):
    assert client.patch("/v1/users/user-2", json={"role": "child"}).status_code == 200
    memory = client.post(
        "/v1/users/user-2/memories",
        json={
            "namespace": "preference",
            "content": "可能喜欢恐龙",
            "source": "assistant_inference",
            "sensitivity": "normal",
            "importance": 0.5,
        },
    ).json()
    assert memory["status"] == "pending_review"
    assert client.get("/v1/users/user-2/memories?include_pending=false").json() == []
    approved = client.post(
        f"/v1/users/user-2/memories/{memory['memory_id']}/approve"
    ).json()
    assert approved["status"] == "active"
