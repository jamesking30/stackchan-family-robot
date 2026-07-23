from __future__ import annotations

import json
import math
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock


class VoiceLatencyTracker:
    """Keep bounded, content-free latency history for field diagnostics."""

    def __init__(self, path: Path | None, max_samples: int = 200) -> None:
        self.path = path
        self.max_samples = max(20, max_samples)
        self._records: deque[dict[str, object]] = deque(maxlen=self.max_samples)
        self._lock = Lock()
        if path is not None and path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines()[-self.max_samples :]:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    self._records.append(record)

    def record(
        self, latency_ms: dict[str, float], *, success: bool, endpoint_reason: str
    ) -> None:
        record = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "success": success,
            "endpoint_reason": endpoint_reason,
            "latency_ms": dict(latency_ms),
        }
        with self._lock:
            self._records.append(record)
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(
                    "\n".join(
                        json.dumps(item, ensure_ascii=False, separators=(",", ":"))
                        for item in self._records
                    )
                    + "\n",
                    encoding="utf-8",
                )

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            records = list(self._records)
        stages = sorted(
            {
                stage
                for record in records
                for stage in dict(record.get("latency_ms", {}))
            }
        )
        percentiles: dict[str, dict[str, float]] = {}
        for stage in stages:
            values = sorted(
                float(dict(record.get("latency_ms", {}))[stage])
                for record in records
                if stage in dict(record.get("latency_ms", {}))
            )
            if values:
                percentiles[stage] = {
                    "p50": self._percentile(values, 0.50),
                    "p95": self._percentile(values, 0.95),
                }
        return {
            "sample_count": len(records),
            "successful_turns": sum(bool(item.get("success")) for item in records),
            "percentiles_ms": percentiles,
            "recent": records[-10:],
        }

    @staticmethod
    def _percentile(values: list[float], quantile: float) -> float:
        index = max(0, math.ceil(len(values) * quantile) - 1)
        return round(values[index], 1)
