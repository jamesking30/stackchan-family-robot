import json
from pathlib import Path

from stackchan_control.metrics import VoiceLatencyTracker


def test_latency_tracker_persists_content_free_percentiles(tmp_path: Path):
    path = tmp_path / "latency.jsonl"
    tracker = VoiceLatencyTracker(path, max_samples=20)
    tracker.record(
        {"asr": 100.0, "first_audio_sent": 400.0},
        success=True,
        endpoint_reason="adaptive_silence",
    )
    tracker.record(
        {"asr": 200.0, "first_audio_sent": 800.0},
        success=True,
        endpoint_reason="device_vad_end",
    )

    snapshot = VoiceLatencyTracker(path).snapshot()

    assert snapshot["sample_count"] == 2
    assert snapshot["percentiles_ms"]["asr"] == {"p50": 100.0, "p95": 200.0}
    raw = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert set(raw) == {"recorded_at", "success", "endpoint_reason", "latency_ms"}
