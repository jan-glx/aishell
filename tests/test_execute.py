import os

from fastapi.testclient import TestClient

TEST_API_TOKEN = os.environ.get("TEST_API_TOKEN", os.environ.get("API_TOKEN", "test-token"))
os.environ["API_TOKEN"] = TEST_API_TOKEN
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

from aishell import app

client = TestClient(app)

def test_execute_date():
    response = client.post(
        "/execute",
        headers={"Authorization": f"Bearer {TEST_API_TOKEN}"},
        json={"command": "date"},
    )
    assert response.status_code == 200
    assert "20" in response.json().get("stdout", "")  # crude check for year


def test_tmux_send_requires_auth():
    response = client.post("/tmux/test-session/send", json={"input": ["echo blocked", "Enter"]})
    assert response.status_code == 401
