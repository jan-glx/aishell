import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

TEST_API_TOKEN = os.environ.get("TEST_API_TOKEN", os.environ.get("API_TOKEN", "test-token"))
os.environ["API_TOKEN"] = TEST_API_TOKEN
TEST_DOMAIN = os.environ.get("TEST_YOUR_DOMAIN", "example.com")
os.environ["YOUR_DOMAIN"] = TEST_DOMAIN
os.environ.pop("OPENAI_API_KEY", None)

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


def test_download_returns_signed_file_url_with_required_headers(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "result.txt").write_text("hello")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    response = client.get(
        "/download",
        params={"filename": "result.txt"},
        headers={"Authorization": f"Bearer {TEST_API_TOKEN}"},
    )

    assert response.status_code == 200
    download_url = response.json()["openaiFileResponse"][0]
    parsed = urlparse(download_url)
    assert parsed.scheme == "https"
    assert parsed.netloc == TEST_DOMAIN
    assert parsed.path == "/download-file/result.txt"
    assert set(parse_qs(parsed.query)) == {"expires", "signature"}

    file_response = client.get(f"{parsed.path}?{parsed.query}")
    assert file_response.status_code == 200
    assert file_response.content == b"hello"
    assert file_response.headers["content-type"].startswith("text/plain")
    assert file_response.headers["content-disposition"] == 'attachment; filename="result.txt"'
    assert file_response.headers["cache-control"] == "no-store"

    head_response = client.head(f"{parsed.path}?{parsed.query}")
    assert head_response.status_code == 200
    assert head_response.headers["content-type"].startswith("text/plain")
    assert head_response.headers["content-disposition"] == 'attachment; filename="result.txt"'


def test_download_file_rejects_tampered_signature(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "result.txt").write_text("hello")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    response = client.get(
        "/download-file/result.txt?expires=4102444800&signature=invalid"
    )

    assert response.status_code == 403


def test_transfer_form_upload_saves_to_input(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    response = client.post(
        "/transfer/form",
        headers={"Authorization": f"Bearer {TEST_API_TOKEN}"},
        files={"file": ("upload.txt", b"hello")},
    )

    assert response.status_code == 200
    assert (tmp_path / "input" / "upload.txt").read_bytes() == b"hello"
    assert response.json() == {
        "filename": "upload.txt",
        "path": "~/input/upload.txt",
    }


def test_upload_alias_saves_to_input(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    response = client.post(
        "/upload",
        headers={"Authorization": f"Bearer {TEST_API_TOKEN}"},
        files={"file": ("legacy.txt", b"hello")},
    )

    assert response.status_code == 200
    assert (tmp_path / "input" / "legacy.txt").read_bytes() == b"hello"
    assert response.json() == {
        "filename": "legacy.txt",
        "path": "~/input/legacy.txt",
    }
