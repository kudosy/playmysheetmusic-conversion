"""The web address of the service, kept apart from Modal so it can be tested
on any computer.

GET  /health            is the service up? (no password)
POST /convert           send file(s), wait, get the notes back
POST /jobs              send file(s), get a job number straight away
GET  /jobs/<number>     ask whether that job is finished, and get the notes

Everything except /health needs the header
    Authorization: Bearer <CONVERSION_SECRET>

Files go in a form field called "files" (repeat it for several photos of one
song, in page order). Optional form fields: "pages" (read at most this many
pages), "top" (read only this share of each page from the top, e.g. 0.33) and
"musicxml" (true to get homr's MusicXML back as well).
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

MAX_FILES = 10  # photos per song (the website allows 10)
MAX_UPLOAD_BYTES = 60 * 1024 * 1024  # all files of one song together
MIN_SECRET_LENGTH = 16

Run = Callable[[list[bytes], "int | None", "float | None", bool], Awaitable[dict]]
Start = Callable[[list[bytes], "int | None", "float | None", bool], Awaitable[str]]
Poll = Callable[[str], Awaitable["dict | None"]]


class NoSuchJob(Exception):
    pass


def build_api(run: Run, start: Start, poll: Poll, service: str = "pms-conversion") -> FastAPI:
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

    return api
