"""Checks for the web address, with the conversion itself replaced by a stand-in.
Skipped when FastAPI is not installed."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from conversion.webapi import NoSuchJob, build_api  # noqa: E402

SECRET = "a-long-secret-for-the-tests"
AUTH = {"Authorization": f"Bearer {SECRET}"}
GOOD = {"ok": True, "title": "", "score": {"bars": [], "notes": []}, "pages": [], "seconds": 0.1}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CONVERSION_SECRET", SECRET)
    seen = {}

    async def run(blobs, pages, top, musicxml):
        seen.update(blobs=blobs, pages=pages, top=top, musicxml=musicxml)
        if blobs[0] == b"not music":
            return {"ok": False, "code": "no_music", "message": "We couldn't find sheet music in this image."}
        return GOOD

    async def start(blobs, pages, top, musicxml):
        return "fc-123"

    async def poll(job):
        if job == "fc-123":
            return None
        if job == "fc-done":
            return GOOD
        raise NoSuchJob(job)

    test_client = TestClient(build_api(run, start, poll))
    test_client.seen = seen
    return test_client


def test_health_needs_no_secret(client):
    assert client.get("/health").json() == {"ok": True, "service": "pms-conversion"}


def test_everything_else_needs_the_secret(client):
    files = {"files": ("a.png", b"x", "image/png")}
    assert client.post("/convert", files=files).status_code == 401
    assert client.post("/convert", files=files, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/jobs", files=files).status_code == 401
    assert client.get("/jobs/fc-123").status_code == 401


def test_locked_when_no_secret_is_set(client, monkeypatch):
    monkeypatch.setenv("CONVERSION_SECRET", "")
    response = client.post("/convert", files={"files": ("a.png", b"x", "image/png")}, headers={"Authorization": "Bearer "})
    assert response.status_code == 503


def test_convert_passes_files_and_options(client):
    files = [("files", ("1.jpg", b"page one", "image/jpeg")), ("files", ("2.jpg", b"page two", "image/jpeg"))]
    response = client.post("/convert", files=files, data={"pages": "1", "top": "0.33"}, headers=AUTH)
    assert response.status_code == 200 and response.json()["ok"] is True
    assert client.seen == {"blobs": [b"page one", b"page two"], "pages": 1, "top": 0.33, "musicxml": False}


def test_no_music_is_a_clear_refusal(client):
    response = client.post("/convert", files={"files": ("a.png", b"not music", "image/png")}, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["code"] == "no_music"


def test_bad_options_and_too_many_files(client):
    one = {"files": ("a.png", b"x", "image/png")}
    assert client.post("/convert", files=one, data={"top": "2"}, headers=AUTH).status_code == 400
    assert client.post("/convert", files=one, data={"pages": "0"}, headers=AUTH).status_code == 400
    many = [("files", (f"{i}.png", b"x", "image/png")) for i in range(11)]
    assert client.post("/convert", files=many, headers=AUTH).status_code == 400


def test_jobs(client):
    started = client.post("/jobs", files={"files": ("a.png", b"x", "image/png")}, headers=AUTH)
    assert started.status_code == 202 and started.json() == {"ok": True, "job": "fc-123"}
    assert client.get("/jobs/fc-123", headers=AUTH).json() == {"ok": True, "status": "working"}
    assert client.get("/jobs/fc-done", headers=AUTH).json()["ok"] is True
    assert client.get("/jobs/nope", headers=AUTH).status_code == 404
