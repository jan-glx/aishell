import os

from fastapi.testclient import TestClient

os.environ.setdefault("API_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

from aishell import app

client = TestClient(app)

def test_execute_date():
    response = client.post(
        "/execute",
        headers={"Authorization": "Bearer test-token"},
        json={"command": "date"},
    )
    assert response.status_code == 200
    assert "20" in response.json().get("stdout", "")  # crude check for year


def test_tmux_send_requires_auth():
    response = client.post("/tmux/test-session/send", json={"input": ["echo blocked", "Enter"]})
    assert response.status_code == 401
