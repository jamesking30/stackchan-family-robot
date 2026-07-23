def test_health_reports_voice_configuration_consistently(client):
    health = client.get("/health").json()

    assert health["ok"] is False
    assert health["voice_configured"] is False
    assert health["voice_ready"] is False
    assert health["wake_word_ready"] is False
    assert health["deployment_path_valid"] is True
    assert health["missing_paths"] == []
