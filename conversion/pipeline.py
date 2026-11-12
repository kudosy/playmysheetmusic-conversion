"""File in, notes out.

    convert([file_bytes, ...]) -> {"ok": True, "score": {...}, "title": ..., "pages": [...]}

Each file is a PDF or a picture. The pages of all files, in the order given,
make up one song.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import numpy as np

from .normalize import MAX_PAGES, UnreadableFile, load_pages
from .notes import musicxml_to_notes, title_from_musicxml
from .recognize import NoMusicFound, read_page

# Messages shown to customers by the website.
MSG_NO_MUSIC = "We couldn't find sheet music in this image."
MSG_UNREADABLE = "We couldn't open this file. Please upload a PDF or a photo."


class ConversionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def crop_top(image: np.ndarray, fraction: float) -> np.ndarray:
    """Keep only the top part of a page (used for the quick preview)."""
    fraction = min(1.0, max(0.05, fraction))
    return image[: max(1, round(image.shape[0] * fraction))]


def read_pages_here(images: list[np.ndarray], with_title: bool) -> list[tuple[str | None, float]]:
    """Read pages one after another on this machine. Returns, for each page,
    its MusicXML (None when no music was found) and the seconds it took."""
    out: list[tuple[str | None, float]] = []
    for index, image in enumerate(images):
        started = time.time()
        try:
            # Only the first page carries the title, and reading it costs time.
            xml, _ = read_page(image, with_title=with_title and index == 0)
        except NoMusicFound:
            xml = None
        out.append((xml, round(time.time() - started, 1)))
    return out


def convert(
    files: list[bytes],
    *,
    max_pages: int | None = None,
    top_fraction: float | None = None,
    with_title: bool = True,
    keep_musicxml: bool = False,
    reader: Callable[[list[np.ndarray], bool], list[tuple[str | None, float]]] = read_pages_here,
) -> dict:
    """Convert the pages of one song.

    max_pages     stop after this many pages (the preview reads only one)
    top_fraction  read only this share of each page, from the top
    keep_musicxml include homr's MusicXML for each page in the result
    reader        how pages are read; the Modal service reads them side by side
    """
    started = time.time()
    limit = MAX_PAGES if not max_pages or max_pages < 1 else min(max_pages, MAX_PAGES)
    try:
        pages = []
        for data in files:
            if len(pages) >= limit:
                break
            pages.extend(load_pages(data, limit - len(pages)))
    except UnreadableFile as error:
        raise ConversionError("unreadable", MSG_UNREADABLE) from error
    if not pages:
        raise ConversionError("unreadable", MSG_UNREADABLE)

    images = [page.image if top_fraction is None else crop_top(page.image, top_fraction) for page in pages]
    results = reader(images, with_title)

    xmls: list[str] = []
    report: list[dict] = []
    for index, (page, image, (xml, seconds)) in enumerate(zip(pages, images, results)):
        entry: dict = {"page": index, "ok": xml is not None, "bars": 0, "notes": 0, "seconds": seconds,
                       "steps": page.steps, "width": int(image.shape[1]), "height": int(image.shape[0])}
        if xml is not None:  # a cover or blank page inside a longer file is skipped
            xmls.append(xml)
            if keep_musicxml:
                entry["musicxml"] = xml
        report.append(entry)

    if not xmls:
        raise ConversionError("no_music", MSG_NO_MUSIC)

    score = musicxml_to_notes(xmls)
    if not score["notes"]:
        raise ConversionError("no_music", MSG_NO_MUSIC)

    # Say how much was found on each page that was read. Bars carry the number
    # of the page they came from, counting every page of the upload.
    read = [entry for entry in report if entry["ok"]]
    for bar in score["bars"]:
        bar["page"] = read[bar["page"]]["page"]
    page_of_bar = {bar["n"]: bar["page"] for bar in score["bars"]}
    for entry in read:
        entry["bars"] = sum(1 for bar in score["bars"] if bar["page"] == entry["page"])
        entry["notes"] = sum(1 for note in score["notes"] if page_of_bar[note["b"]] == entry["page"])

    return {
        "ok": True,
        "title": title_from_musicxml(xmls[0]),
        "score": score,
        "pages": report,
        "seconds": round(time.time() - started, 1),
    }
