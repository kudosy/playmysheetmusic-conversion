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


PAGE_IMAGE_WIDTH = 2000  # widest page picture kept for the editor
PAGE_IMAGE_QUALITY = 78


def read_pages_here(images: list[np.ndarray], with_title: bool) -> list[tuple[str | None, float, dict | None]]:
    """Read pages one after another on this machine. Returns, for each page,
    its MusicXML (None when no music was found), the seconds it took, and
    where its lines and bars sit on the page."""
    out: list[tuple[str | None, float, dict | None]] = []
    for index, image in enumerate(images):
        started = time.time()
        try:
            # Only the first page carries the title, and reading it costs time.
            xml, _, layout = read_page(image, with_title=with_title and index == 0)
        except NoMusicFound:
            xml, layout = None, None
        out.append((xml, round(time.time() - started, 1), layout))
    return out


def page_picture(image: np.ndarray) -> bytes:
    """The cleaned-up page as a JPEG, for the editor to show line by line."""
    import cv2

    if image.shape[1] > PAGE_IMAGE_WIDTH:
        scale = PAGE_IMAGE_WIDTH / image.shape[1]
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ok, data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, PAGE_IMAGE_QUALITY])
    if not ok:
        raise ValueError("could not save the page picture")
    return data.tobytes()


def line_boxes(score: dict, layouts: dict[int, dict]) -> list[dict]:
    """One entry per line of music: which bars it holds and, when known, where
    it sits on its page. Positions are shares of the page (0 to 1), so they
    hold for any size of the page picture.

        {"page", "line", "first", "last", "box": [left, top, right, bottom], "barx": [...]}

    `barx` has one more entry than the line has bars: the left edge of the
    first bar, then the right edge of each bar. It is None when the bar
    positions could not be trusted."""
    groups: dict[tuple[int, int], list[int]] = {}
    for bar in score["bars"]:
        groups.setdefault((bar["page"], bar["line"]), []).append(bar["n"])
    lines_on_page: dict[int, int] = {}
    for page, _ in groups:
        lines_on_page[page] = lines_on_page.get(page, 0) + 1

    out = []
    for (page, line), bars in sorted(groups.items()):
        entry: dict = {"page": page, "line": line, "first": bars[0], "last": bars[-1], "box": None, "barx": None}
        layout = layouts.get(page)
        # Positions are only used when homr's lines match the lines in the notes one for one.
        if layout and len(layout["lines"]) == lines_on_page[page] and layout["w"] > 0 and layout["h"] > 0:
            found = layout["lines"]
            width, height = layout["w"], layout["h"]
            x0, y0, x1, y1 = found[line]["box"]
            tall = y1 - y0
            # Room for notes above and below the staves, words and tempo marks:
            # up to halfway to the neighbouring line, at most 60% of this line's height.
            above = (y0 - found[line - 1]["box"][3]) / 2 if line > 0 else tall * 0.35
            below = (found[line + 1]["box"][1] - y1) / 2 if line + 1 < len(found) else tall * 0.35
            above, below = min(max(above, 0), tall * 0.6), min(max(below, 0), tall * 0.6)
            side = (x1 - x0) * 0.03
            share = lambda value, whole: round(min(1.0, max(0.0, value / whole)), 4)  # noqa: E731
            entry["box"] = [share(x0 - side, width), share(y0 - above, height),
                            share(x1 + side, width), share(y1 + below, height)]
            barx = found[line]["barx"]
            if barx and len(barx) == len(bars) + 1:
                entry["barx"] = [share(x, width) for x in barx]
        out.append(entry)
    return out


def convert(
    files: list[bytes],
    *,
    max_pages: int | None = None,
    top_fraction: float | None = None,
    with_title: bool = True,
    keep_musicxml: bool = False,
    keep_page_images: bool = False,
    reader: Callable[[list[np.ndarray], bool], list[tuple[str | None, float, dict | None]]] = read_pages_here,
) -> dict:
    """Convert the pages of one song.

    max_pages     stop after this many pages (the preview reads only one)
    top_fraction  read only this share of each page, from the top
    keep_musicxml include homr's MusicXML for each page in the result
    keep_page_images  add "images": the cleaned-up pages as JPEG bytes, one per
                  entry of "pages" (None for a page with no music)
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
    layouts: dict[int, dict] = {}
    for index, (page, image, (xml, seconds, layout)) in enumerate(zip(pages, images, results)):
        entry: dict = {"page": index, "ok": xml is not None, "bars": 0, "notes": 0, "seconds": seconds,
                       "steps": page.steps, "width": int(image.shape[1]), "height": int(image.shape[0])}
        if xml is not None:  # a cover or blank page inside a longer file is skipped
            xmls.append(xml)
            if layout:
                layouts[index] = layout
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

    score["lines"] = line_boxes(score, layouts)

    result = {
        "ok": True,
        "title": title_from_musicxml(xmls[0]),
        "score": score,
        "pages": report,
        "seconds": round(time.time() - started, 1),
    }
    if keep_page_images:
        result["images"] = [page_picture(image) if entry["ok"] else None for image, entry in zip(images, report)]
    return result
