"""The web address of the service, kept apart from Modal so it can be tested
on any computer.

GET  /health            is the service up? (no password)
POST /convert           send file(s), wait, get the notes back
POST /jobs              send file(s), get a job number straight away
GET  /jobs/<number>     ask whether that job is finished, and get the notes
POST /songs             convert purchased songs and call the website back
POST /preview           read the start of one uploaded page, wait, get the notes

Everything except /health needs the header
    Authorization: Bearer <CONVERSION_SECRET>

Files go in a form field called "files" (repeat it for several photos of one
song, in page order). Optional form fields: "pages" (read at most this many
pages), "top" (read only this share of each page from the top, e.g. 0.33) and
"musicxml" (true to get homr's MusicXML back as well).

/songs is what the website uses after a purchase. It takes JSON:

    {"callback_url": "https://.../api/conversion/callback",
     "page_url": "https://.../api/conversion/page",        (optional)
     "songs": [{"song_id": "...", "files": ["https://...page1", "https://...page2"]}]}

and answers at once. Each song is then converted on its own, and the result
is posted to callback_url with the same Authorization header:

    {"song_id": "...", "ok": true, "score": {...}, "title": "...", "pages": [...]}
    {"song_id": "...", "ok": false, "code": "no_music", "message": "..."}

With page_url, the cleaned-up picture of every page is first posted there as
a JPEG (page_url?song=<id>&page=<number>), for the note editor.

/reread is what the website uses when the owner of a song has corrected
where its lines sit on the pages (see conversion/reread.py). It takes JSON:

    {"song_id": "...", "callback_url": "https://...", "page_url": "https://...",
     "pages": [{"page": 0, "url": "https://...page picture", "turn": 0}],
     "lines": [{"keep": 0}, {"page": 0, "box": [0.05, 0.3, 0.95, 0.45]}],
     "score": {"bars": [...], "notes": [...], "lines": [...]}}

and answers at once. The new notes are posted to callback_url:

    {"song_id": "...", "ok": true, "score": {...}, "bar_map": {"4": 8}, "turned": [0]}

/preview is what the website uses at checkout. It takes JSON
{"url": "https://...page1", "top": 0.33}, reads only that share of the first
page from the top, and answers with the usual result.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .jobs import MAX_FILES_PER_SONG, BadAddress, check_address

MAX_FILES = 10  # photos per song (the website allows 10)
MAX_UPLOAD_BYTES = 60 * 1024 * 1024  # all files of one song together
MIN_SECRET_LENGTH = 16

Run = Callable[[list[bytes], "int | None", "float | None", bool], Awaitable[dict]]
Start = Callable[[list[bytes], "int | None", "float | None", bool], Awaitable[str]]
Poll = Callable[[str], Awaitable["dict | None"]]
StartSong = Callable[[str, list[str], str, "str | None"], Awaitable[None]]
Preview = Callable[[str, float], Awaitable[dict]]
StartReread = Callable[[dict], Awaitable[None]]
MAX_SONGS_PER_ORDER = 40


class SongRequest(BaseModel):
    song_id: str = Field(min_length=1, max_length=100)
    files: list[str] = Field(min_length=1, max_length=MAX_FILES_PER_SONG)


class PreviewRequest(BaseModel):
    url: str = Field(max_length=2000)
    top: float = Field(default=0.33, ge=0.05, le=1)


class OrderRequest(BaseModel):
    callback_url: str = Field(max_length=2000)
    page_url: str | None = Field(default=None, max_length=2000)  # where to send page pictures
    songs: list[SongRequest] = Field(min_length=1, max_length=MAX_SONGS_PER_ORDER)


class PagePicture(BaseModel):
    page: int = Field(ge=0, le=200)
    url: str = Field(max_length=2000)
    turn: int = 0


class RereadRequest(BaseModel):
    song_id: str = Field(min_length=1, max_length=100)
    callback_url: str = Field(max_length=2000)
    page_url: str | None = Field(default=None, max_length=2000)
    pages: list[PagePicture] = Field(max_length=40)
    lines: list[dict] = Field(min_length=1, max_length=120)
    score: dict


class NoSuchJob(Exception):
    pass


def build_api(run: Run, start: Start, poll: Poll, start_song: StartSong | None = None,
              preview: Preview | None = None, start_reread: StartReread | None = None, service: str = "pms-conversion") -> FastAPI:
    api = FastAPI(title="PlayMySheetMusic conversion", docs_url=None, redoc_url=None, openapi_url=None)

    @api.middleware("http")
    async def require_secret(request: Request, call_next):
        """Turn away anyone without the shared secret before reading a single
        uploaded byte."""
        if request.url.path != "/health":
            secret = os.environ.get("CONVERSION_SECRET", "")
            if len(secret) < MIN_SECRET_LENGTH:
                return JSONResponse({"detail": "The service has no shared secret yet."}, status_code=503)
            given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(given.encode(), secret.encode()):
                return JSONResponse({"detail": "Wrong or missing secret."}, status_code=401)
        return await call_next(request)

    async def gather(files: list[UploadFile]) -> list[bytes]:
        if not files or len(files) > MAX_FILES:
            raise HTTPException(400, f"Send between 1 and {MAX_FILES} files.")
        blobs, total = [], 0
        for upload in files:
            data = await upload.read()
            total += len(data)
            if total > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "These files are too large.")
            blobs.append(data)
        return blobs

    def options(pages: int | None, top: float | None) -> tuple[int | None, float | None]:
        if pages is not None and pages < 1:
            raise HTTPException(400, '"pages" must be 1 or more.')
        if top is not None and not 0.05 <= top <= 1:
            raise HTTPException(400, '"top" must be between 0.05 and 1.')
        return pages, top

    def reply(result: dict) -> JSONResponse:
        return JSONResponse(result, status_code=200 if result.get("ok") else 422)

    @api.get("/health")
    def health() -> dict:
        return {"ok": True, "service": service}

    @api.post("/convert")
    async def convert_now(
        files: list[UploadFile] = File(...),
        pages: int | None = Form(None),
        top: float | None = Form(None),
        musicxml: bool = Form(False),
    ):
        pages, top = options(pages, top)
        return reply(await run(await gather(files), pages, top, musicxml))

    @api.post("/jobs")
    async def start_job(
        files: list[UploadFile] = File(...),
        pages: int | None = Form(None),
        top: float | None = Form(None),
        musicxml: bool = Form(False),
    ):
        pages, top = options(pages, top)
        job = await start(await gather(files), pages, top, musicxml)
        return JSONResponse({"ok": True, "job": job}, status_code=202)

    @api.get("/jobs/{job}")
    async def job_status(job: str):
        try:
            result = await poll(job)
        except NoSuchJob:
            raise HTTPException(404, "No such job.")
        except Exception:
            return JSONResponse(
                {"ok": False, "code": "failed", "message": "The conversion stopped unexpectedly."}, status_code=500
            )
        if result is None:
            return JSONResponse({"ok": True, "status": "working"}, status_code=202)
        return reply(result)

    @api.post("/songs")
    async def convert_songs(order: OrderRequest):
        if start_song is None:
            raise HTTPException(501, "Not available here.")
        try:
            check_address(order.callback_url)
            if order.page_url:
                check_address(order.page_url)
            for song in order.songs:
                for url in song.files:
                    check_address(url)
        except BadAddress as error:
            raise HTTPException(400, str(error))
        for song in order.songs:
            await start_song(song.song_id, song.files, order.callback_url, order.page_url)
        return JSONResponse({"ok": True, "started": len(order.songs)}, status_code=202)

    @api.post("/reread")
    async def read_lines_again(wanted: RereadRequest):
        if start_reread is None:
            raise HTTPException(501, "Not available here.")
        try:
            check_address(wanted.callback_url)
            if wanted.page_url:
                check_address(wanted.page_url)
            for page in wanted.pages:
                check_address(page.url)
                if page.turn not in (0, 90, 180, 270):
                    raise BadAddress("Pages turn by quarters.")
        except BadAddress as error:
            raise HTTPException(400, str(error))
        if not isinstance(wanted.score.get("bars"), list) or not isinstance(wanted.score.get("notes"), list):
            raise HTTPException(400, "The song's notes are missing.")
        await start_reread(wanted.model_dump())
        return JSONResponse({"ok": True, "started": 1}, status_code=202)

    @api.post("/preview")
    async def preview_page(wanted: PreviewRequest):
        if preview is None:
            raise HTTPException(501, "Not available here.")
        try:
            check_address(wanted.url)
        except BadAddress as error:
            raise HTTPException(400, str(error))
        return reply(await preview(wanted.url, wanted.top))

    return api
