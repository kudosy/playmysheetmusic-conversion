"""Checks for the after-purchase job: fetch the pages, convert, report back.
A small web server on this computer plays both the file store and the website."""

import http.server
import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conversion.jobs import BadAddress, check_address, run_song  # noqa: E402

SECRET = "a-long-secret-for-the-tests"
GOOD = {"ok": True, "title": "T", "score": {"bars": [{"n": 0}], "notes": [{"id": "0R0.0.0"}]},
        "pages": [{"page": 0, "ok": True, "bars": 1, "notes": 1, "steps": [], "musicxml": "<big/>"}], "seconds": 1}


@pytest.fixture
def site():
    state = {"calls": [], "fail_first": 0, "status": 200}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/files/missing"):
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"PAGE:" + self.path.encode())

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["calls"].append({"auth": self.headers.get("Authorization"), "body": body})
            if state["fail_first"] > 0:
                state["fail_first"] -= 1
                self.send_response(503)
            else:
                self.send_response(state["status"])
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["base"] = f"http://127.0.0.1:{server.server_address[1]}"
    yield state
    server.shutdown()


def run(site, urls, convert, waits=(0, 0, 0)):
    return run_song("song-1", urls, convert, site["base"] + "/api/conversion/callback", SECRET,
                    allow_plain_http=True, report_waits=waits)


def test_pages_are_fetched_in_order_and_the_result_is_reported(site):
    seen = []

    def convert(files):
        seen.extend(files)
        return GOOD

    out = run(site, [site["base"] + "/files/1.jpg", site["base"] + "/files/2.jpg"], convert)
    assert seen == [b"PAGE:/files/1.jpg", b"PAGE:/files/2.jpg"]
    assert out["reported"] is True and len(site["calls"]) == 1
    call = site["calls"][0]
    assert call["auth"] == f"Bearer {SECRET}"
    assert call["body"]["song_id"] == "song-1" and call["body"]["ok"] is True
    assert call["body"]["score"] == GOOD["score"]
    assert "musicxml" not in call["body"]["pages"][0]  # only the summary is sent back


def test_no_music_is_reported_as_a_refusal(site):
    refusal = {"ok": False, "code": "no_music", "message": "We couldn't find sheet music in this image."}
    run(site, [site["base"] + "/files/1.jpg"], lambda files: refusal)
    body = site["calls"][0]["body"]
    assert (body["ok"], body["code"], body["message"]) == (False, "no_music", refusal["message"])


def test_a_missing_file_and_a_crash_are_reported_too(site):
    run(site, [site["base"] + "/files/missing.jpg"], lambda files: GOOD)
    assert site["calls"][0]["body"]["code"] == "download"

    def crash(files):
        raise RuntimeError("boom")

    run(site, [site["base"] + "/files/1.jpg"], crash)
    assert site["calls"][1]["body"]["code"] == "failed"


def test_reporting_is_tried_again_when_the_site_is_busy(site):
    site["fail_first"] = 2
    out = run(site, [site["base"] + "/files/1.jpg"], lambda files: GOOD)
    assert out["reported"] is True and len(site["calls"]) == 3


def test_reporting_stops_when_the_site_refuses(site):
    site["status"] = 401
    out = run(site, [site["base"] + "/files/1.jpg"], lambda files: GOOD)
    assert out["reported"] is False and len(site["calls"]) == 1


def test_without_a_callback_the_result_is_just_returned(site):
    out = run_song("s", [site["base"] + "/files/1.jpg"], lambda files: GOOD, allow_plain_http=True)
    assert out["ok"] is True and "reported" not in out and site["calls"] == []


def test_only_https_addresses_are_accepted():
    assert check_address("https://example.com/x")
    for bad in ("http://example.com/x", "file:///etc/passwd", "ftp://x/y", "example.com"):
        with pytest.raises(BadAddress):
            check_address(bad)
    out = run_song("s", ["http://example.com/a.jpg"], lambda files: GOOD)
    assert out["ok"] is False and out["code"] == "failed"
