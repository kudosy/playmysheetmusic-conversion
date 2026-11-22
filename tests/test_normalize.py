"""Checks for the page clean-up. Needs OpenCV; skipped without it."""

import os
import sys

import numpy as np
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
    assert out.steps in ([], ["faded print showing through the paper"]) and same_way_up(out.image, page())


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


def _png(image):
    return cv2.imencode(".png", image)[1].tobytes()


def test_a_photo_of_two_pages_is_read_as_two_pages():
    """Two book pages side by side: the engine would mix their lines, so the
    photo is split at the gap between them, left page first."""
    page = cv2.imread(SAMPLE)
    gap = np.full((page.shape[0], page.shape[1] // 12, 3), 255, np.uint8)
    spread = np.hstack([page, gap, page])
    pages = N.load_pages(_png(spread))
    assert len(pages) == 2
    assert "split a photo of two pages" in pages[0].steps
    for half in pages:
        assert abs(half.image.shape[1] - spread.shape[1] / 2) < spread.shape[1] * 0.1


def test_one_wide_page_is_not_split():
    """A single page photographed wide (its staff lines run straight across)."""
    page = cv2.imread(SAMPLE)
    wide = cv2.resize(page, (page.shape[1] * 2, page.shape[0]), interpolation=cv2.INTER_LINEAR)
    pages = N.load_pages(_png(wide))
    assert len(pages) == 1 and "split a photo of two pages" not in pages[0].steps


def test_an_upright_single_page_is_not_split():
    assert len(N.load_pages(_png(cv2.imread(SAMPLE)))) == 1


def test_print_showing_through_the_paper_is_faded_and_real_print_kept():
    """A hymnal page: dark music, and faint staffs from the next page showing
    through. The faint ones become paper; the dark ones stay as they are."""
    page = np.full((900, 1200, 3), 200, np.uint8)
    for y in range(100, 150, 10):
        cv2.line(page, (100, y), (1100, y), (30, 30, 30), 2)  # real staff
    for y in range(600, 650, 10):
        cv2.line(page, (100, y), (1100, y), (178, 178, 178), 2)  # show-through
    out, changed = N.fade_show_through(page)
    assert changed
    assert out[600:650, 100:1100].min() >= 190  # gone
    assert out[100:150, 100:1100].min() <= 40  # kept


def test_a_dim_photo_is_left_alone():
    """Without strong dark print there is nothing to compare against: change nothing."""
    page = np.full((900, 1200, 3), 170, np.uint8)
    for y in range(100, 150, 10):
        cv2.line(page, (100, y), (1100, y), (110, 110, 110), 2)
    out, changed = N.fade_show_through(page)
    assert not changed and (out == page).all()
