"""Convert a purchased song and tell the website when it is done.

The website sends, for each song, links to its uploaded pages and an address
to call back. This module downloads the pages, converts them, and posts the
result to that address with the shared secret. It only uses the standard
library, apart from the conversion itself.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from urllib.parse import urlparse

MAX_FILE_BYTES = 25 * 1024 * 1024  # the site allows 20 MB per file
MAX_FILES_PER_SONG = 10
DOWNLOAD_TIMEOUT = 60
REPORT_TIMEOUT = 30
REPORT_WAITS = (0, 3, 10, 30, 60)  # seconds before each try at reporting back

MSG_FAILED = "Something went wrong while reading this song."
MSG_DOWNLOAD = "We couldn't fetch the uploaded file."


class BadAddress(ValueError):
    pass


def check_address(url: str, allow_plain_http: bool = False) -> str:
    """Only web addresses, and only encrypted ones outside of tests."""
    parts = urlparse(url)
    if parts.scheme != "https" and not (allow_plain_http and parts.scheme == "http"):
        raise BadAddress("Addresses must start with https://")
    if not parts.netloc:
        raise BadAddress("Not a web address.")
    return url


def fetch_file(url: str, allow_plain_http: bool = False) -> bytes:
    check_address(url, allow_plain_http)
    request = urllib.request.Request(url, headers={"User-Agent": "pms-conversion"})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
        data = response.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("file too large")
    return data


def report(callback_url: str, secret: str, payload: dict, allow_plain_http: bool = False,
           waits: tuple[float, ...] = REPORT_WAITS) -> bool:
    """Post the outcome to the website. Tries a few times; True once it lands."""
    check_address(callback_url, allow_plain_http)
    body = json.dumps(payload, separators=(",", ":")).encode()
    for wait in waits:
        if wait:
            time.sleep(wait)
        request = urllib.request.Request(
            callback_url, data=body, method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {secret}",
                     "User-Agent": "pms-conversion"},
        )
        try:
            with urllib.request.urlopen(request, timeout=REPORT_TIMEOUT) as response:
                if 200 <= response.status < 300:
                    return True
        except urllib.error.HTTPError as error:
            if 400 <= error.code < 500 and error.code not in (408, 429):
                return False  # the site refused it; trying again will not help
        except Exception:
            pass
    return False


def run_song(
    song_id: str,
    urls: list[str],
    convert: Callable[[list[bytes]], dict],
    callback_url: str | None = None,
    secret: str = "",
    allow_plain_http: bool = False,
    report_waits: tuple[float, ...] = REPORT_WAITS,
) -> dict:
    """Download, convert, report. `convert` takes the files and returns the
    usual result dictionary. Returns what was (or would have been) reported."""
    started = time.time()
    try:
        if not urls or len(urls) > MAX_FILES_PER_SONG:
            raise BadAddress("wrong number of files")
        try:
            files = [fetch_file(url, allow_plain_http) for url in urls]
        except BadAddress:
            raise
        except Exception:
            payload = {"ok": False, "code": "download", "message": MSG_DOWNLOAD}
        else:
            result = convert(files)
            if result.get("ok"):
                payload = {"ok": True, "score": result["score"], "title": result.get("title", ""),
                           "pages": [{k: page.get(k) for k in ("page", "ok", "bars", "notes", "steps")}
                                     for page in result.get("pages", [])]}
            else:
                payload = {"ok": False, "code": result.get("code", "failed"),
                           "message": result.get("message", MSG_FAILED)}
    except Exception:
        payload = {"ok": False, "code": "failed", "message": MSG_FAILED}
    payload["song_id"] = song_id
    payload["seconds"] = round(time.time() - started, 1)
    if callback_url:
        try:
            payload["reported"] = report(callback_url, secret, payload, allow_plain_http, report_waits)
        except BadAddress:
            payload["reported"] = False
    return payload
