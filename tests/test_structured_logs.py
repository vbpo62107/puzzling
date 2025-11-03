from __future__ import annotations

import importlib
import json
import sys


def reload_monitoring(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_DIRECTORY", str(tmp_path))
    if "monitoring" in sys.modules:
        del sys.modules["monitoring"]
    monitoring = importlib.import_module("monitoring")
    monitoring.setup_logging()
    return monitoring


def test_activity_log_writes_jsonl(tmp_path, monkeypatch):
    monitoring = reload_monitoring(tmp_path, monkeypatch)

    monitoring.log_activity(42, "admin", "/search_logs", "status=ok note=done")

    json_path = tmp_path / "activity.jsonl"
    data = json_path.read_text(encoding="utf-8").strip().splitlines()
    assert data, "expected activity.jsonl to contain entries"
    entry = json.loads(data[-1])
    assert entry["user_id"] == 42
    assert entry["role"] == "admin"
    assert entry["action"] == "/search_logs"
    assert entry["command"] == "/search_logs"
    assert entry["status"] == "ok"
    assert entry["note"] == "done"
    assert entry["detail"] == "status=ok note=done"


def test_query_structured_logs_filters(tmp_path, monkeypatch):
    monitoring = reload_monitoring(tmp_path, monkeypatch)

    monitoring.log_activity(1, "user", "/start", "status=ok")
    monitoring.log_activity(2, "user", "receive_url", "url=http://example.com")

    results = monitoring.query_structured_logs(
        log_type="activity",
        user_id=1,
        command="/start",
        extra_filters={"status": "ok"},
    )
    assert len(results) == 1
    assert results[0]["user_id"] == 1

    summary = monitoring.summarize_logs(results)
    assert summary["count"] == 1
    assert summary["top_users"] == [("1", 1)]
    assert summary["top_commands"] == [("/start", 1)]

