"""Read one page image with homr and return its MusicXML.

homr (https://github.com/liebharc/homr) is the recognition engine. It is
licensed AGPL-3.0, which is why this service lives in its own public
repository.
"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import threading

import cv2
import numpy as np

_lock = threading.Lock()  # homr keeps state in module globals; one page at a time
_ready = False

# What homr says when a picture has no music it can find.
_NO_MUSIC_MARKERS = ("No staffs found", "No noteheads or clefs found")


class NoMusicFound(Exception):
    """The page was opened but no sheet music could be found on it."""


def prepare(with_title: bool = True) -> None:
    """Download the recognition models if they are not there yet. Called when
    the service image is built so that no request has to wait for it."""
    global _ready
    if _ready:
        return
    from homr.main import download_weights

    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        download_weights(False, False, False)
        if with_title:
            from homr.title_detection import download_ocr_weights

            download_ocr_weights()
    _ready = True


def _layout(systems, parts, to_input, shape) -> dict:
    """Where each line of music sits on the page, and where its bars begin
    and end, in pixels of the picture that was read.

    `systems` are homr's lines of music; `parts` its symbols, which carry a
    rough position each. A line gets bar positions only when they are
    complete and in order, otherwise None (the editor then spaces bars evenly).
    """
    height, width = int(shape[0]), int(shape[1])
    boxes = []
    for system in systems:
        x0, y0 = to_input((min(s.min_x for s in system.staffs), min(s.min_y for s in system.staffs)))
        x1, y1 = to_input((max(s.max_x for s in system.staffs), max(s.max_y for s in system.staffs)))
        boxes.append([float(x0), float(y0), float(x1), float(y1)])

    # Bar-line positions per line, from every part that has them.
    seen: list[list[list[float]]] = [[] for _ in boxes]  # line -> bar line number -> x values
    first_note: list[list[float]] = [[] for _ in boxes]
    for part in parts:
        line, bar = 0, 0
        for symbol in part:
            kind = symbol.rhythm
            if kind == "newline":
                line, bar = line + 1, 0
                continue
            if line >= len(boxes):
                break
            where = getattr(symbol, "image_coordinates", None)
            x = float(where[0]) if where is not None else None
            if kind.startswith("barline") or kind.startswith("repeat") or kind.startswith("bolddoublebarline") or kind.startswith("doublebarline"):
                while len(seen[line]) <= bar:
                    seen[line].append([])
                if x is not None:
                    seen[line][bar].append(x)
                bar += 1
            elif x is not None and bar == 0 and (kind.startswith("note") or kind.startswith("rest")):
                first_note[line].append(x)

    lines = []
    for index, box in enumerate(boxes):
        barx = None
        ends = [sorted(xs)[len(xs) // 2] if xs else None for xs in seen[index]]
        if ends:
            ends[-1] = box[2]  # the last bar ends where the staff ends
        if ends and all(e is not None for e in ends):
            span = box[2] - box[0]
            start = min(first_note[index]) - 0.02 * span if first_note[index] else box[0] + 0.08 * span
            start = max(box[0], min(start, ends[0] - 0.02 * span))
            candidate = [start] + ends
            inside = all(box[0] - 0.03 * span <= x <= box[2] + 0.03 * span for x in candidate)
            rising = all(b - a > 0.01 * span for a, b in zip(candidate, candidate[1:]))
            if inside and rising:
                barx = [round(x, 1) for x in candidate]
        lines.append({"box": [round(v, 1) for v in box], "barx": barx})
    return {"w": width, "h": height, "lines": lines}


def read_page(image: np.ndarray, with_title: bool = True) -> tuple[str, str, dict]:
    """Run homr on one page (BGR image). Returns its MusicXML, homr's log,
    and the layout of the page (see _layout)."""
    import xml.etree.ElementTree as ET

    import onnxruntime as ort
    from homr.main import ProcessingConfig, detect_staffs_in_image
    from homr.music_xml_generator import XmlGeneratorArguments, generate_xml
    from homr.staff_parsing import parse_staffs
    from homr.system_repair import repair_systems
    from homr.transformer.configs import Config

    prepare(with_title)
    ort.set_default_logger_severity(3)
    config = ProcessingConfig(
        False,  # debug pictures
        False,  # cache
        False,  # write staff positions
        False,  # read staff positions
        -1,  # all staves
        False,  # transformer on GPU
        False,  # segmentation on GPU
        False,  # CoreML encoder
        with_title,
    )
    transformer = Config()
    transformer.use_gpu_inference = False
    transformer.use_coreml_encoder = False
    log = io.StringIO()
    with _lock, tempfile.TemporaryDirectory(prefix="pms-") as folder:
        path = os.path.join(folder, "page.png")
        cv2.imwrite(path, image)
        debug = None
        try:
            with contextlib.redirect_stderr(log), contextlib.redirect_stdout(log):
                # The same steps as homr's own process_image, kept apart here so
                # that the positions of lines and bars can be read off as well.
                systems, working, debug, title_future, _, to_input = detect_staffs_in_image(path, config)
                systems = repair_systems(debug, systems)
                parts = parse_staffs(debug, systems, working, selected_staff=-1, config=transformer,
                                     page_to_input_image=to_input)
                title = title_future.result(60) if with_title else ""
                xml = generate_xml(XmlGeneratorArguments(False, None, None), parts, title)
            text = ET.tostring(xml, encoding="unicode", xml_declaration=True)
            try:
                layout = _layout(systems, parts, to_input, image.shape)
            except Exception:
                layout = {"w": int(image.shape[1]), "h": int(image.shape[0]), "lines": []}
            return text, log.getvalue(), layout
        except Exception as error:
            if any(marker in str(error) for marker in _NO_MUSIC_MARKERS):
                raise NoMusicFound(str(error)) from error
            raise
        finally:
            if debug is not None:
                with contextlib.redirect_stderr(log), contextlib.redirect_stdout(log):
                    debug.clean_debug_files_from_previous_runs()
