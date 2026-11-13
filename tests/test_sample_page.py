"""Reads the sample page with the real engine (slow: about 20 seconds).
Run with:  python -m pytest tests -m slow     Skipped when homr is not installed."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("homr")

from conversion import ConversionError, convert  # noqa: E402

SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample", "sample_tune.png")

from tests.sample.expected import problems  # noqa: E402


@pytest.mark.slow
def test_sample_page_is_read_correctly():
    with open(SAMPLE, "rb") as handle:
        result = convert([handle.read()], with_title=False)
    score = result["score"]
    assert problems(score) == []
    assert [bar["line"] for bar in score["bars"]] == [0, 0, 0, 0, 1, 1, 1, 1]
    # Where the two lines and their bars sit on the page.
    first, second = score["lines"]
    assert (first["first"], first["last"], second["first"], second["last"]) == (0, 3, 4, 7)
    assert first["box"][3] <= second["box"][1] + 0.001 and first["box"][0] < 0.1 and first["box"][2] > 0.9
    for line in (first, second):
        assert len(line["barx"]) == 5 and line["barx"] == sorted(line["barx"])
        widths = [b - a for a, b in zip(line["barx"], line["barx"][1:])]
        assert max(widths) < 2.2 * min(widths)  # four bars of similar width on the page


@pytest.mark.slow
def test_two_pages_make_one_song():
    with open(SAMPLE, "rb") as handle:
        page = handle.read()
    result = convert([page, page], with_title=False)
    assert problems(result["score"], repeats=2) == []
    assert [bar["page"] for bar in result["score"]["bars"]] == [0] * 8 + [1] * 8
    assert [(p["bars"], p["notes"]) for p in result["pages"]] == [(8, 39), (8, 39)]


@pytest.mark.slow
def test_a_picture_without_music_is_refused():
    import cv2
    import numpy as np

    blank = np.full((900, 700, 3), 255, np.uint8)
    cv2.putText(blank, "Shopping list", (60, 200), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 3)
    ok, png = cv2.imencode(".png", blank)
    with pytest.raises(ConversionError) as error:
        convert([png.tobytes()], with_title=False)
    assert error.value.code == "no_music"


def test_a_file_that_is_not_a_picture_is_refused():
    with pytest.raises(ConversionError) as error:
        convert([b"this is not a picture"])
    assert error.value.code == "unreadable"
