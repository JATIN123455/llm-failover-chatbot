import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:////tmp/test-chat.db"
os.environ["LLM_API_KEYS"] = "demo_key_1,demo_key_2"
os.environ["ENABLE_FAILURE_SIMULATION"] = "true"

from fastapi.testclient import TestClient

import app as backend


def setup_function():
    backend.DATABASE_PATH = tempfile.mktemp(suffix=".db")
    backend.init_db()
    backend.CACHE._values.clear()


def test_health():
    with TestClient(backend.app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["keys_configured"] == 2


def test_chat_cache_and_history():
    with TestClient(backend.app) as client:
        first = client.post("/chat", json={"session_id": "s-cache", "message": "What is the capital of France?"})
        second = client.post("/chat", json={"session_id": "s-cache-2", "message": "What is the capital of France?"})
        history = client.get("/sessions/s-cache/messages")

    assert first.status_code == 200
    assert first.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert second.status_code == 200
    assert second.json()["metadata"]["resolution_source"] == "LLM_CACHE"
    assert first.json()["reply"] == second.json()["reply"]
    assert history.status_code == 200
    assert len(history.json()["messages"]) == 2


def test_failover_header():
    with TestClient(backend.app) as client:
        response = client.post(
            "/chat",
            json={"session_id": "s-failover", "message": "Hello"},
            headers={"X-Simulate-Failure": "key_1=429"},
        )
    assert response.status_code == 200
    assert response.json()["metadata"]["key_id"] == "key_2"
    assert response.json()["metadata"]["fallback_attempts"] == 1


def test_all_keys_failed():
    with TestClient(backend.app) as client:
        response = client.post(
            "/chat",
            json={"session_id": "s-failed", "message": "Hello"},
            headers={"X-Simulate-Failure": "*=429"},
        )
    assert response.status_code == 503
    assert response.json()["detail"] == "all providers unavailable"


def test_unknown_session():
    with TestClient(backend.app) as client:
        response = client.get("/sessions/does-not-exist/messages")
    assert response.status_code == 404
