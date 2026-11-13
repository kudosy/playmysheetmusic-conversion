"""Checks for the page clean-up. Needs OpenCV; skipped without it."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

cv2 = pytest.importorskip("cv2")

from conversion import normalize as N  # noqa: E402

SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample", "sample_tune.png")


def same_way_up(a, b) -> bool:
    """True when two pictures of the page show it the same way round."""
    import numpy as np

    small = [cv2.resize(cv2.cvtColor(x, cv2.COLOR_BGR2GRAY), (240, 100)).astype(float) for x in (a, b)]
    x, y = [v - v.mean() for v in small]
    return float((x * y).mean() / (np.std(x) * np.std(y))) > 0.5


def page():
    return cv2.imread(SAMPLE)


def test_an_upright_page_is_left_alone():
    out = N.normalize(page())
    assert out.steps == [] and same_way_up(out.image, page())


def test_a_sideways_page_is_turned_upright_whichever_way_it_lies():
    for turn in (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE):
        out = N.normalize(cv2.rotate(page(), turn))
        assert "turned a sideways page" in out.steps and same_way_up(out.image, page())


def test_a_tilted_page_is_straightened():
    out = N.normalize(N._rotate(page(), 4.0))
    assert any(step.startswith("straightened") for step in out.steps) and same_way_up(out.image, page())


def test_a_level_page_is_never_turned_upside_down(monkeypatch):
    # Even when the guess says "upside down", a page that arrived level is trusted.
    monkeypatch.setattr(N, "_upside_down", lambda ink: True)
    out = N.normalize(page())
    assert "turned an upside-down page" not in out.steps and same_way_up(out.image, page())
