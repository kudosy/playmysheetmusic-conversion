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
from urllib.parse import urlencode, urlparse

MAX_FILE_BYTES = 25 * 1024 * 1024  # the site allows 20 MB per file
MAX_FILES_PER_SONG = 10
DOWNLOAD_TIMEOUT = 60
REPORT_TIMEOUT = 30
REPORT_WAITS = (0, 3, 10, 30, 60)  # seconds before each try at reporting back
PAGE_WAITS = (0, 3, 10)  # the same for sending a page picture

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


def _post(url: str, secret: str, body: bytes, content_type: str, allow_plain_http: bool,
          waits: tuple[float, ...]) -> bool:
    """Post something to the website. Tries a few times; True once it lands."""
    check_address(url, allow_plain_http)
    for wait in waits:
        if wait:
            time.sleep(wait)
        request = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": content_type, "Authorization": f"Bearer {secret}",
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


def report(callback_url: str, secret: str, payload: dict, allow_plain_http: bool = False,
           waits: tuple[float, ...] = REPORT_WAITS) -> bool:
    """Post the outcome of a song to the website."""
    body = json.dumps(payload, separators=(",", ":")).encode()
    return _post(callback_url, secret, body, "application/json", allow_plain_http, waits)


def send_page(page_url: str, secret: str, song_id: str, page: int, jpeg: bytes,
              allow_plain_http: bool = False, waits: tuple[float, ...] = PAGE_WAITS) -> bool:
    """Send the cleaned-up picture of one page to the website, which keeps it
    for the note editor."""
    joiner = "&" if "?" in page_url else "?"
    url = f"{page_url}{joiner}{urlencode({'song': song_id, 'page': page})}"
    return _post(url, secret, jpeg, "image/jpeg", allow_plain_http, waits)


def run_song(
    song_id: str,
    urls: list[str],
    convert: Callable[[list[bytes]], dict],
    callback_url: str | None = None,
    secret: str = "",
    allow_plain_http: bool = False,
    report_waits: tuple[float, ...] = REPORT_WAITS,
    page_url: str | None = None,
) -> dict:
    """Download, convert, report. `convert` takes the files and returns the
    usual result dictionary. Returns what was (or would have been) reported.

    When `page_url` is given and the result has "images" (the cleaned-up
    pages), each is sent there first; "page_images" in the report lists the
    pages that arrived."""
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
                stored: list[int] = []
                if page_url:
                    for number, jpeg in enumerate(result.get("images") or []):
                        try:
                            if jpeg and send_page(page_url, secret, song_id, number, jpeg, allow_plain_http,
                                                  tuple(w for w in PAGE_WAITS if w <= max(report_waits, default=0))
                                                  or (0,)):
                                stored.append(number)
                        except Exception:
                            pass  # the song is still usable without its pictures
                payload = {"ok": True, "score": result["score"], "title": result.get("title", ""),
                           "page_images": stored,
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


MAX_PAGES_PER_REREAD = 40


def run_reread(
    song_id: str,
    pages: list[dict],
    lines: list[dict],
    score: dict,
    reread: Callable[[dict, dict, list, dict], dict],
    callback_url: str | None = None,
    secret: str = "",
    allow_plain_http: bool = False,
    report_waits: tuple[float, ...] = REPORT_WAITS,
    page_url: str | None = None,
) -> dict:
    """Read some lines of a song again (see conversion/reread.py) and tell
    the website. `pages` is [{"page", "url", "turn"}]: the page pictures the
    website keeps, with a quarter turn where the owner asked for one.
    `reread` takes (page pictures, turns, lines, score) and returns the usual
    kind of result. Turned pages are sent back to `page_url` first, and the
    song is only reported as done when every one of them arrived, so that the
    boxes and the pictures never disagree."""
    started = time.time()
    try:
        if len(pages) > MAX_PAGES_PER_REREAD:
            raise BadAddress("too many pages")
        try:
            pictures = {int(entry["page"]): fetch_file(entry["url"], allow_plain_http) for entry in pages}
        except BadAddress:
            raise
        except Exception:
            payload = {"ok": False, "code": "download", "message": MSG_DOWNLOAD}
        else:
            turns = {int(entry["page"]): int(entry["turn"]) for entry in pages if entry.get("turn")}
            result = reread(pictures, turns, lines, score)
            if result.get("ok"):
                waits = tuple(w for w in PAGE_WAITS if w <= max(report_waits, default=0)) or (0,)
                sent = []
                for number, jpeg in sorted((result.get("images") or {}).items()):
                    if not page_url or not send_page(page_url, secret, song_id, number, jpeg, allow_plain_http, waits):
                        break
                    sent.append(number)
                if len(sent) == len(result.get("images") or {}):
                    payload = {"ok": True, "score": result["score"], "bar_map": result["bar_map"],
                               "read": result["read"], "empty": result["empty"], "turned": sent}
                else:
                    payload = {"ok": False, "code": "failed", "message": MSG_FAILED, "turned": sent}
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
