import pytest
from fastapi.testclient import TestClient
from aishell.__main__ import app

client = TestClient(app)

def test_execute_date():
    response = client.post("/execute", json={"command": "date"})
    assert response.status_code == 200
    assert "20" in response.json().get("stdout", "")  # crude check for year
