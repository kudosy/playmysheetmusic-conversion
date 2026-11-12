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


def read_page(image: np.ndarray, with_title: bool = True) -> tuple[str, str]:
    """Run homr on one page (BGR image). Returns (MusicXML text, homr's log)."""
    import onnxruntime as ort
    from homr.main import ProcessingConfig, process_image
    from homr.music_xml_generator import XmlGeneratorArguments

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
    log = io.StringIO()
    with _lock, tempfile.TemporaryDirectory(prefix="pms-") as folder:
        path = os.path.join(folder, "page.png")
        cv2.imwrite(path, image)
        try:
            with contextlib.redirect_stderr(log), contextlib.redirect_stdout(log):
                xml_path = process_image(path, config, XmlGeneratorArguments(False, None, None))
            with open(xml_path, encoding="utf-8") as handle:
                return handle.read(), log.getvalue()
        except Exception as error:
            if any(marker in str(error) for marker in _NO_MUSIC_MARKERS):
                raise NoMusicFound(str(error)) from error
            raise
