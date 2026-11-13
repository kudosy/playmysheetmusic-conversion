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

    songs = []

    async def start_song(song_id, urls, callback_url, page_url):
        songs.append((song_id, urls, callback_url) + ((page_url,) if page_url else ()))

    async def preview(url, top):
        songs.append(("preview", url, top))
        return GOOD if "good" in url else {"ok": False, "code": "no_music", "message": "We couldn't find sheet music in this image."}

    test_client = TestClient(build_api(run, start, poll, start_song, preview))
    test_client.seen = seen
    test_client.songs = songs
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


def test_purchased_songs_are_started_one_by_one(client):
    order = {"callback_url": "https://playmysheetmusic.com/api/conversion/callback",
             "songs": [{"song_id": "a", "files": ["https://x.co/1.jpg", "https://x.co/2.jpg"]},
                       {"song_id": "b", "files": ["https://x.co/3.pdf"]}]}
    assert client.post("/songs", json=order).status_code == 401
    response = client.post("/songs", json=order, headers=AUTH)
    assert response.status_code == 202 and response.json() == {"ok": True, "started": 2}
    assert client.songs == [("a", ["https://x.co/1.jpg", "https://x.co/2.jpg"], order["callback_url"]),
                            ("b", ["https://x.co/3.pdf"], order["callback_url"])]


def test_bad_orders_are_refused(client):
    good = {"callback_url": "https://site/cb", "songs": [{"song_id": "a", "files": ["https://x.co/1.jpg"]}]}
    for change in ({"callback_url": "http://site/cb"}, {"songs": []},
                   {"songs": [{"song_id": "a", "files": []}]},
                   {"songs": [{"song_id": "a", "files": ["file:///etc/passwd"]}]},
                   {"songs": [{"song_id": "a", "files": ["https://x.co/1.jpg"] * 11}]}):
        response = client.post("/songs", json={**good, **change}, headers=AUTH)
        assert response.status_code in (400, 422), change
    assert client.songs == []


def test_checkout_preview(client):
    assert client.post("/preview", json={"url": "https://x.co/good.jpg"}).status_code == 401
    ok = client.post("/preview", json={"url": "https://x.co/good.jpg"}, headers=AUTH)
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert client.songs == [("preview", "https://x.co/good.jpg", 0.33)]
    no = client.post("/preview", json={"url": "https://x.co/list.jpg", "top": 0.5}, headers=AUTH)
    assert no.status_code == 422 and no.json()["code"] == "no_music"
    assert client.post("/preview", json={"url": "http://x.co/a.jpg"}, headers=AUTH).status_code == 400
    assert client.post("/preview", json={"url": "https://x.co/a.jpg", "top": 3}, headers=AUTH).status_code == 422


def test_page_picture_address_is_passed_on(client):
    order = {"callback_url": "https://site/cb", "page_url": "https://site/page",
             "songs": [{"song_id": "a", "files": ["https://x.co/1.jpg"]}]}
    assert client.post("/songs", json=order, headers=AUTH).status_code == 202
    assert client.songs == [("a", ["https://x.co/1.jpg"], "https://site/cb", "https://site/page")]
    assert client.post("/songs", json={**order, "page_url": "ftp://site/page"}, headers=AUTH).status_code == 400
