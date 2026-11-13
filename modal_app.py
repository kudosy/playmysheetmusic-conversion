"""The conversion service on Modal.

Deployed by .github/workflows/deploy.yml on every push to main:

    modal deploy modal_app.py

Three pieces:

  convert_song   file(s) in, notes out. Called from the web address below, or
                 directly with the Modal Python library.
  convert_purchased_song
                 what runs after a purchase: fetches a song's uploaded pages,
                 converts them and posts the result back to the website.
  read_one_page  reads a single page; convert_song uses it to read the pages
                 of a long song side by side instead of one after another.
  web            the web address the website talks to.

The service sleeps when nobody is using it and wakes on the next request.
"""

import modal

APP_NAME = "pms-conversion"
SECRET_NAME = "pms-conversion"  # holds CONVERSION_SECRET, shared with the website

app = modal.App(APP_NAME)


def _download_models() -> None:
    """Runs once while the image is built, so no customer waits for it."""
    from homr.main import download_weights
    from homr.title_detection import download_ocr_weights

    download_weights(False, False, False)
    download_ocr_weights()


image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libgl1", "libglib2.0-0")
    .pip_install_from_requirements("requirements.txt")
    .pip_install("fastapi[standard]==0.162.5")
    .run_function(_download_models)
    .add_local_python_source("conversion")
)


@app.function(image=image, cpu=4, memory=4096, timeout=900, max_containers=8, scaledown_window=120)
def read_one_page(png: bytes, with_title: bool) -> tuple[str | None, float, dict | None]:
    """Read one cleaned-up page. Returns its MusicXML (None if no music was
    found), the seconds it took, and where its lines and bars sit."""
    import time

    import cv2
    import numpy as np

    from conversion.recognize import NoMusicFound, read_page

    started = time.time()
    picture = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    try:
        xml, _, layout = read_page(picture, with_title=with_title)
    except NoMusicFound:
        xml, layout = None, None
    return xml, round(time.time() - started, 1), layout


@app.function(image=image, cpu=4, memory=4096, timeout=1800, max_containers=4, scaledown_window=300)
def convert_song(
    files: list[bytes],
    max_pages: int | None = None,
    top_fraction: float | None = None,
    keep_musicxml: bool = False,
    keep_page_images: bool = False,
) -> dict:
    """Convert one song. Always returns a dictionary: {"ok": True, "score": ...}
    or {"ok": False, "code": ..., "message": ...}. With keep_page_images the
    result also has "images", the cleaned-up pages as JPEG bytes."""
    import cv2

    from conversion.pipeline import ConversionError, convert, read_pages_here

    def reader(images, with_title):
        if len(images) == 1:
            return read_pages_here(images, with_title)
        pngs = [cv2.imencode(".png", picture)[1].tobytes() for picture in images]
        titles = [with_title and index == 0 for index in range(len(images))]
        return list(read_one_page.map(pngs, titles))

    try:
        return convert(files, max_pages=max_pages, top_fraction=top_fraction,
                       keep_musicxml=keep_musicxml, keep_page_images=keep_page_images, reader=reader)
    except ConversionError as error:
        return {"ok": False, "code": error.code, "message": error.message}


@app.function(image=image, secrets=[modal.Secret.from_name(SECRET_NAME)], cpu=4, memory=4096,
              timeout=1800, max_containers=8, scaledown_window=120)
def convert_purchased_song(
    song_id: str,
    urls: list[str],
    callback_url: str | None = None,
    max_pages: int | None = None,
    top_fraction: float | None = None,
    page_url: str | None = None,
) -> dict:
    """Download a song's pages from their links, convert them, and (when a
    callback address is given) post the result to the website. Returns what
    was reported. With max_pages and top_fraction it reads only the start of
    the song, which is how the checkout preview uses it."""
    import os

    from conversion.jobs import run_song

    def convert(files):
        return convert_song.local(files, max_pages, top_fraction, False, bool(page_url))

    return run_song(song_id, urls, convert, callback_url, os.environ.get("CONVERSION_SECRET", ""),
                    page_url=page_url)


@app.function(image=image, secrets=[modal.Secret.from_name(SECRET_NAME)], cpu=0.5, memory=1024,
              timeout=900, max_containers=2, scaledown_window=300)
@modal.concurrent(max_inputs=20)
@modal.asgi_app()
def web():
    """The web address the website talks to. See conversion/webapi.py for
    what it offers."""
    from conversion.webapi import NoSuchJob, build_api

    async def run(blobs, pages, top, musicxml):
        return await convert_song.remote.aio(blobs, pages, top, musicxml)

    async def start(blobs, pages, top, musicxml):
        call = await convert_song.spawn.aio(blobs, pages, top, musicxml)
        return call.object_id

    async def poll(job):
        try:
            call = await modal.FunctionCall.from_id.aio(job)
            return await call.get.aio(timeout=0)
        except TimeoutError:
            return None  # still working
        except (modal.exception.NotFoundError, modal.exception.InvalidError) as error:
            raise NoSuchJob(job) from error

    async def start_song(song_id, urls, callback_url, page_url):
        await convert_purchased_song.spawn.aio(song_id, urls, callback_url, None, None, page_url)

    async def preview(url, top):
        return await convert_purchased_song.remote.aio("preview", [url], None, 1, top)

    return build_api(run, start, poll, start_song, preview, service=APP_NAME)
