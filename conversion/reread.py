"""Read some lines of a song again, after its owner has corrected where the
lines sit on the page.

The owner sees each page with a box around every line of music. They can
move, resize, add and remove boxes and turn a page. Lines whose boxes were
left alone keep their notes. Every other box is cut out of the page picture
and read on its own, and the song is put back together in the new order.

    reread(pages, turns, lines, old_score) -> {"ok": True, "score": ..., "bar_map": ...}

pages   {page number: JPEG bytes} for every page that has a line to read
        or is to be turned
turns   {page number: 90 | 180 | 270}, clockwise
lines   every line of the song in its new order, each either
          {"keep": <index into old_score["lines"]>}             or
          {"page": <number>, "box": [left, top, right, bottom]}
        with the box as shares (0 to 1) of the page after turning
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable

import numpy as np

from .notes import key_alter, midi_number, musicxml_to_notes
from .pipeline import ConversionError, MSG_NO_MUSIC, MSG_UNREADABLE, page_picture, read_pages_here

MAX_LINES = 120
MIN_BOX_SHARE = 0.01  # a box must cover at least this share of the page each way
TURNS = (90, 180, 270)


class BadLayout(ValueError):
    pass


def turn_picture(image: np.ndarray, degrees: int) -> np.ndarray:
    import cv2

    how = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
    return cv2.rotate(image, how[degrees])


def _check(lines: list[dict], old: dict, turns: dict[int, int]) -> None:
    if not isinstance(lines, list) or not lines or len(lines) > MAX_LINES:
        raise BadLayout("wrong number of lines")
    old_lines = old.get("lines") or []
    kept: set[int] = set()
    for entry in lines:
        if "keep" in entry and entry["keep"] is not None:
            index = entry["keep"]
            if not isinstance(index, int) or not 0 <= index < len(old_lines) or index in kept:
                raise BadLayout("no such line to keep")
            if old_lines[index]["page"] in turns:
                raise BadLayout("a line on a turned page cannot be kept")
            kept.add(index)
            continue
        box, page = entry.get("box"), entry.get("page")
        if not isinstance(page, int) or page < 0:
            raise BadLayout("a box needs a page")
        if (not isinstance(box, list) or len(box) != 4
                or not all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in box)
                or box[2] - box[0] < MIN_BOX_SHARE or box[3] - box[1] < MIN_BOX_SHARE):
            raise BadLayout("a box is not on its page")
    for degrees in turns.values():
        if degrees not in TURNS:
            raise BadLayout("pages turn by quarters")


def _cut(image: np.ndarray, box: list[float]) -> np.ndarray:
    height, width = image.shape[:2]
    x0, y0, x1, y1 = box
    return image[max(0, int(y0 * height)): min(height, round(y1 * height)),
                 max(0, int(x0 * width)): min(width, round(x1 * width))]


def _split(score: dict, layout: dict | None, box: list[float]) -> list[dict]:
    """The lines found inside one box, each {"bars", "notes", "box", "barx"}
    with bars numbered from 0. Nearly always one; two when the box was drawn
    around two lines."""
    groups: dict[int, list[dict]] = {}
    for bar in score["bars"]:
        groups.setdefault(bar["line"], []).append(bar)
    found = (layout or {}).get("lines") or []
    width, height = (layout or {}).get("w") or 0, (layout or {}).get("h") or 0
    x0, y0, x1, y1 = box
    out = []
    for number in sorted(groups):
        bars = groups[number]
        first = bars[0]["n"]
        here: dict = {"bars": [dict(bar, n=bar["n"] - first) for bar in bars],
                      "notes": [dict(note, b=note["b"] - first) for note in score["notes"]
                                if first <= note["b"] <= bars[-1]["n"]],
                      "box": box, "barx": None}
        matched = len(found) == len(groups) and width > 0 and height > 0
        if matched:
            barx = found[number]["barx"]
            if barx and len(barx) == len(bars) + 1:
                here["barx"] = [round(x0 + (x / width) * (x1 - x0), 4) for x in barx]
            if len(groups) > 1:  # give each line its own share of the box
                top = y0 if number == 0 else (found[number - 1]["box"][3] + found[number]["box"][1]) / 2 / height
                low = y1 if number + 1 == len(found) else \
                    (found[number]["box"][3] + found[number + 1]["box"][1]) / 2 / height
                if number > 0:
                    top = y0 + top * (y1 - y0)
                if number + 1 < len(found):
                    low = y0 + low * (y1 - y0)
                here["box"] = [x0, round(top, 4), x1, round(low, 4)]
        elif len(groups) > 1:
            here["box"] = None
        out.append(here)
    return out


def _set_key(piece: dict, wanted: int) -> None:
    """A line read on its own has only its own key signature to go by. When
    that disagrees with the rest of the song, the song wins."""
    keys = {bar["key"] for bar in piece["bars"]}
    if len(keys) != 1:
        return  # the key changes inside this line; leave it as read
    read = keys.pop()
    if read == wanted:
        return
    for note in piece["notes"]:
        letter = note["s"] % 7
        if note["a"] == key_alter(read, letter):
            note["a"] = key_alter(wanted, letter)
            note["m"] = midi_number(note["s"], note["a"])
    for bar in piece["bars"]:
        bar["key"] = wanted


def _usual_len(bars: list[dict]) -> int | float | None:
    lengths = [bar["len"] for bar in bars]
    return Counter(lengths).most_common(1)[0][0] if lengths else None


def reread(
    pages: dict[int, bytes],
    turns: dict[int, int],
    lines: list[dict],
    old: dict,
    reader: Callable[[list[np.ndarray], bool], list[tuple[str | None, float, dict | None]]] = read_pages_here,
) -> dict:
    """See the top of this file. The result also has "read" (how many lines
    were read again), "empty" (positions in `lines` where no music was found)
    and "images" ({page: JPEG} for every turned page)."""
    import cv2

    started = time.time()
    _check(lines, old, turns)
    old_lines = old.get("lines") or []

    pictures: dict[int, np.ndarray] = {}
    for page, data in pages.items():
        picture = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if picture is None:
            raise ConversionError("unreadable", MSG_UNREADABLE)
        if page in turns:
            picture = turn_picture(picture, turns[page])
        pictures[page] = picture

    # Read every changed box, side by side where the reader can.
    wanted = [index for index, entry in enumerate(lines) if entry.get("keep") is None]
    for index in wanted:
        if lines[index]["page"] not in pictures:
            raise BadLayout("a page picture is missing")
    crops = [_cut(pictures[lines[index]["page"]], lines[index]["box"]) for index in wanted]
    results = dict(zip(wanted, reader(crops, False))) if crops else {}

    # Put the song together, line after line.
    old_notes: dict[int, list[dict]] = {}
    for note in old["notes"]:
        old_notes.setdefault(note["b"], []).append(note)
    old_bars = {bar["n"]: bar for bar in old["bars"]}

    pieces: list[dict] = []  # {"page", "bars", "notes", "box", "barx", "old_first" | None}
    empty: list[int] = []
    for index, entry in enumerate(lines):
        if entry.get("keep") is not None:
            line = old_lines[entry["keep"]]
            numbers = range(line["first"], line["last"] + 1)
            pieces.append({"page": line["page"], "box": line.get("box"), "barx": line.get("barx"),
                           "old_first": line["first"],
                           "bars": [dict(old_bars[n], n=n - line["first"]) for n in numbers],
                           "notes": [dict(note, b=note["b"] - line["first"]) for n in numbers
                                     for note in old_notes.get(n, [])]})
            continue
        xml, _, layout = results[index]
        score = None
        if xml is not None:
            usual = _usual_len(pieces[-1]["bars"]) if pieces else None
            score = musicxml_to_notes([xml], bar_len=usual, pickup=not pieces)
        if not score or not score["notes"]:
            empty.append(index)
            continue
        for part in _split(score, layout, [round(float(v), 4) for v in entry["box"]]):
            pieces.append({"page": entry["page"], "old_first": None, **part})

    if not any(piece["notes"] for piece in pieces):
        raise ConversionError("no_music", MSG_NO_MUSIC)

    # Keys: a line read again takes the key of the nearest line that was kept.
    kept_at = [i for i, piece in enumerate(pieces) if piece["old_first"] is not None]
    fresh = [piece for piece in pieces if piece["old_first"] is None]
    common = Counter(piece["bars"][0]["key"] for piece in fresh).most_common(1)[0][0] if fresh else 0
    for i, piece in enumerate(pieces):
        if piece["old_first"] is not None:
            continue
        before = [k for k in kept_at if k < i]
        after = [k for k in kept_at if k > i]
        if before:
            _set_key(piece, pieces[before[-1]]["bars"][-1]["key"])
        elif after:
            _set_key(piece, pieces[after[0]]["bars"][0]["key"])
        else:
            _set_key(piece, common)

    bars: list[dict] = []
    notes: list[dict] = []
    new_lines: list[dict] = []
    bar_map: dict[str, int] = {}
    position: int | float = 0
    lines_on_page: dict[int, int] = {}
    for piece in pieces:
        page = piece["page"]
        line = lines_on_page.get(page, 0)
        lines_on_page[page] = line + 1
        first = len(bars)
        for bar in piece["bars"]:
            number = first + bar["n"]
            if piece["old_first"] is not None:
                bar_map[str(piece["old_first"] + bar["n"])] = number
            bars.append(dict(bar, n=number, start=position, page=page, line=line))
            position = round(position + bar["len"], 3)
            if position == int(position):
                position = int(position)
        for note in piece["notes"]:
            # A note's name starts with its bar number; the rest stays.
            was = piece["old_first"] + note["b"] if piece["old_first"] is not None else note["b"]
            tail = note["id"][len(str(was)):]
            notes.append(dict(note, b=first + note["b"], id=f"{first + note['b']}{tail}"))
        new_lines.append({"page": page, "line": line, "first": first, "last": len(bars) - 1,
                          "box": piece["box"], "barx": piece["barx"]})

    notes.sort(key=lambda note: note["b"])
    return {
        "ok": True,
        "score": {"bars": bars, "notes": notes, "lines": new_lines},
        "bar_map": bar_map,
        "read": len(wanted),
        "empty": empty,
        "images": {page: page_picture(pictures[page]) for page in turns if page in pictures},
        "seconds": round(time.time() - started, 1),
    }
