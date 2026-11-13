"""Checks for the positions of lines and bars (no recognition engine needed)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conversion.notes import musicxml_to_notes  # noqa: E402
from tests.test_notes import NEW_LINE, attributes, note, score  # noqa: E402


def two_lines():
    bar = note("C", 4, 4, typ="whole")
    first = attributes(divisions=1, time=(4, 4), clefs=((1, "G"),)) + bar
    return musicxml_to_notes([score([first, bar, NEW_LINE + bar, bar, bar])])


def boxes(layouts):
    import importlib

    pipeline = importlib.import_module("conversion.pipeline")
    return pipeline.line_boxes(two_lines(), layouts)


def test_lines_without_positions_still_list_their_bars():
    import pytest

    pytest.importorskip("numpy")
    lines = boxes({})
    assert [(line["first"], line["last"], line["box"], line["barx"]) for line in lines] == [(0, 1, None, None), (2, 4, None, None)]


def test_positions_become_shares_of_the_page():
    import pytest

    pytest.importorskip("numpy")
    layout = {"w": 1000, "h": 2000, "lines": [
        {"box": [100, 200, 900, 400], "barx": [150, 500, 900]},
        {"box": [100, 800, 900, 1000], "barx": [150, 400, 900]},  # three bars but only two positions: not trusted
    ]}
    first, second = boxes({0: layout})
    assert first["box"] == [0.076, 0.065, 0.924, 0.26]  # padded: 35% above the first line, at most 60% of its height below
    assert first["barx"] == [0.15, 0.5, 0.9]
    assert second["box"][1] == 0.34 and second["barx"] is None


def test_positions_are_dropped_when_the_lines_do_not_match():
    import pytest

    pytest.importorskip("numpy")
    layout = {"w": 1000, "h": 2000, "lines": [{"box": [100, 200, 900, 400], "barx": None}]}  # one line found, two in the notes
    assert all(line["box"] is None for line in boxes({0: layout}))
