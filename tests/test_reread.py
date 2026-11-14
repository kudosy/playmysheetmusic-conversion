"""Checks for reading lines again after their boxes were corrected.
The quick ones use a stand-in for the engine; the slow ones the real thing."""

import os
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conversion.jobs import run_reread  # noqa: E402
from conversion.notes import musicxml_to_notes  # noqa: E402
from conversion.pipeline import ConversionError  # noqa: E402
from conversion.reread import BadLayout, reread  # noqa: E402
from tests.test_jobs import SECRET, site  # noqa: E402,F401
from tests.test_notes import NEW_LINE, attributes, note, score  # noqa: E402

SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample", "sample_tune.png")


def tune(letters, key=0, first_line=True, alter=None):
    """Bars of four quarter notes; a new line every two bars."""
    measures = []
    for index in range(0, len(letters), 4):
        body = "".join(note(letter, 4, 2, alter=alter) for letter in letters[index:index + 4])
        if index == 0:
            body = attributes(divisions=2, key=key, time=(4, 4) if first_line else None, clefs=((1, "G"),)) + body
        elif index % 8 == 0:
            body = NEW_LINE + body
        measures.append(body)
    return score(measures)


def old_song():
    """Three lines of two bars on one page, in one sharp (F sharp)."""
    out = musicxml_to_notes([tune("CDEF" "GABC" "DEFG" "ABCD" "EFGA" "BCDE", key=1)])
    out["lines"] = [{"page": 0, "line": i, "first": 2 * i, "last": 2 * i + 1,
                     "box": [0.05, 0.1 + 0.3 * i, 0.95, 0.3 + 0.3 * i], "barx": [0.1, 0.5, 0.95]} for i in range(3)]
    return out


def picture(width=400, height=600):
    image = np.full((height, width, 3), 255, np.uint8)
    cv2.rectangle(image, (10, 10), (60, 40), (0, 0, 0), -1)  # a mark in the top left corner
    return cv2.imencode(".jpg", image)[1].tobytes()


def stand_in(*xmls):
    seen = []

    def reader(images, with_title):
        seen.extend(images)
        layout = {"w": 100, "h": 50, "lines": [{"box": [5, 5, 95, 45], "barx": [10, 50, 95]}]}
        return [(xml, 1.0, layout if xml else None) for xml in xmls]

    reader.seen = seen
    return reader


def letters(result, line):
    entry = result["score"]["lines"][line]
    return "".join("CDEFGAB"[n["s"] % 7] for n in result["score"]["notes"] if entry["first"] <= n["b"] <= entry["last"])


def test_one_line_is_read_again_and_the_others_keep_their_notes():
    old = old_song()
    # The middle line is read again; on its own its key signature is missed (read as no sharps).
    reader = stand_in(tune("DEFG" "ABCC", key=0, first_line=False))
    new_box = [0.05, 0.38, 0.95, 0.62]
    out = reread({0: picture()}, {}, [{"keep": 0}, {"page": 0, "box": new_box}, {"keep": 2}], old, reader)
    assert out["ok"] and out["read"] == 1 and out["empty"] == [] and out["images"] == {}
    assert reader.seen[0].shape[:2] == (round(0.62 * 600) - int(0.38 * 600), round(0.95 * 400) - int(0.05 * 400))
    new = out["score"]
    assert [bar["n"] for bar in new["bars"]] == list(range(6))
    assert [bar["start"] for bar in new["bars"]] == [0, 32, 64, 96, 128, 160]
    assert (letters(out, 0), letters(out, 1), letters(out, 2)) == ("CDEFGABC", "DEFGABCC", "EFGABCDE")
    # kept lines are untouched, note for note
    for line in (0, 2):
        first, last = new["lines"][line]["first"], new["lines"][line]["last"]
        assert [n for n in new["notes"] if first <= n["b"] <= last] == [n for n in old["notes"] if first <= n["b"] <= last]
    assert out["bar_map"] == {"0": 0, "1": 1, "4": 4, "5": 5}
    # the song's key wins over what the single line seemed to say
    middle = [n for n in new["notes"] if n["b"] in (2, 3)]
    assert [n["a"] for n in middle if n["s"] % 7 == 3] == [1] and all(bar["key"] == 1 for bar in new["bars"])
    assert [n["m"] for n in middle if n["s"] % 7 == 3] == [66]
    assert new["lines"][1] == {"page": 0, "line": 1, "first": 2, "last": 3, "box": new_box,
                               "barx": [round(0.05 + 0.1 * 0.9, 4), 0.5, round(0.05 + 0.95 * 0.9, 4)]}


def test_lines_can_be_removed_added_and_bars_renumbered():
    old = old_song()
    three = score([attributes(divisions=2, key=1, clefs=((1, "G"),)) + "".join(note("G", 4, 2) for _ in range(4)),
                   "".join(note("A", 4, 2) for _ in range(4)), "".join(note("B", 4, 2) for _ in range(4))])
    reader = stand_in(three)
    # the first line is thrown out, a new one of three bars is added at the end
    out = reread({0: picture()}, {}, [{"keep": 1}, {"keep": 2}, {"page": 0, "box": [0.1, 0.8, 0.9, 0.95]}], old, reader)
    new = out["score"]
    assert len(new["bars"]) == 7 and [(l["first"], l["last"], l["line"]) for l in new["lines"]] == [(0, 1, 0), (2, 3, 1), (4, 6, 2)]
    assert out["bar_map"] == {"2": 0, "3": 1, "4": 2, "5": 3}
    assert all(n["id"].startswith(f"{n['b']}R") for n in new["notes"])
    assert len({n["id"] for n in new["notes"]}) == len(new["notes"])
    assert letters(out, 0) == "DEFGABCD" and letters(out, 2) == "GGGGAAAABBBB"
    assert new["lines"][2]["barx"] is None  # three bars but only two positions: not trusted


def test_a_box_drawn_around_two_lines_becomes_two_lines():
    old = old_song()
    out = reread({0: picture()}, {}, [{"keep": 0}, {"page": 0, "box": [0.1, 0.4, 0.9, 0.9]}], old,
                 stand_in(tune("DEFG" "ABCD" "EFGA" "BCDE", key=1, first_line=False)))
    assert [(l["first"], l["last"], l["line"]) for l in out["score"]["lines"]] == [(0, 1, 0), (2, 3, 1), (4, 5, 2)]
    assert letters(out, 2) == "EFGABCDE"


def test_a_short_first_bar_in_the_middle_of_a_song_is_not_a_pick_up():
    old = old_song()
    short = score([attributes(divisions=2, key=1, clefs=((1, "G"),)) + note("G", 4, 2), "".join(note("A", 4, 2) for _ in range(4))])
    out = reread({0: picture()}, {}, [{"keep": 0}, {"page": 0, "box": [0.1, 0.4, 0.9, 0.6]}], old, stand_in(short))
    assert [bar["len"] for bar in out["score"]["bars"]] == [32, 32, 32, 32]


def test_a_box_with_no_music_is_left_out_and_reported():
    old = old_song()
    out = reread({0: picture()}, {}, [{"keep": 0}, {"page": 0, "box": [0.1, 0.4, 0.9, 0.6]}, {"keep": 2}], old, stand_in(None))
    assert out["empty"] == [1] and len(out["score"]["lines"]) == 2 and out["bar_map"] == {"0": 0, "1": 1, "4": 2, "5": 3}
    with pytest.raises(ConversionError):
        reread({0: picture()}, {}, [{"page": 0, "box": [0.1, 0.4, 0.9, 0.6]}], old, stand_in(None))


def test_a_box_the_engine_chokes_on_does_not_spoil_the_rest(monkeypatch):
    import conversion.reread as module

    def choke(images, with_title):
        if images[0].shape[0] < 100:
            raise RuntimeError("engine fell over")
        return [(tune("DEFG" "ABCD", key=1, first_line=False), 1.0, None)]

    monkeypatch.setattr(module, "read_pages_here", choke)
    out = reread({0: picture()}, {}, [{"keep": 0}, {"page": 0, "box": [0.1, 0.4, 0.9, 0.5]}, {"page": 0, "box": [0.1, 0.5, 0.9, 0.9]}], old_song())
    assert out["empty"] == [1] and len(out["score"]["lines"]) == 2 and letters(out, 1) == "DEFGABCD"


def test_a_turned_page_is_turned_before_it_is_cut_and_sent_back():
    old = old_song()
    reader = stand_in(tune("CDEF" "GABC", key=1), tune("DEFG" "ABCD", key=1, first_line=False))
    lines = [{"page": 0, "box": [0.0, 0.0, 0.5, 0.5]}, {"page": 0, "box": [0.5, 0.5, 1.0, 1.0]}]
    out = reread({0: picture()}, {0: 180}, lines, old, reader)
    # the corner mark was top left; after half a turn it is bottom right
    assert reader.seen[0].min() > 200 and reader.seen[1][-40:-10, -60:-10].max() < 60
    back = cv2.imdecode(np.frombuffer(out["images"][0], np.uint8), cv2.IMREAD_COLOR)
    assert back.shape[:2] == (600, 400) and back[-30, -30].max() < 60 and back[30, 30].min() > 200
    sideways = reread({0: picture()}, {0: 90}, lines, old, reader)
    assert cv2.imdecode(np.frombuffer(sideways["images"][0], np.uint8), cv2.IMREAD_COLOR).shape[:2] == (400, 600)
    assert out["bar_map"] == {} and len(out["score"]["bars"]) == 4


def test_bad_layouts_are_refused():
    old = old_song()
    for lines, turns in (
        ([], {}),
        ([{"keep": 7}], {}),
        ([{"keep": 0}, {"keep": 0}], {}),
        ([{"keep": 0}], {0: 180}),  # a kept line on a turned page
        ([{"page": 0, "box": [0.5, 0.1, 0.2, 0.3]}], {}),
        ([{"page": 0, "box": [0.1, 0.1, 1.2, 0.3]}], {}),
        ([{"page": 3, "box": [0.1, 0.1, 0.9, 0.3]}], {}),  # no picture for that page
        ([{"page": 0, "box": [0.1, 0.1, 0.9, 0.3]}], {0: 45}),
    ):
        with pytest.raises(BadLayout):
            reread({0: picture()}, turns, lines, old, stand_in(None))


def test_the_job_fetches_reports_and_sends_turned_pages_first(site):  # noqa: F811
    seen = {}

    def again(pictures, turns, lines, old):
        seen.update(pictures=pictures, turns=turns, lines=lines)
        return {"ok": True, "score": {"bars": [], "notes": [], "lines": []}, "bar_map": {"1": 0}, "read": 1,
                "empty": [], "images": {2: b"TURNED"}}

    pages = [{"page": 0, "url": site["base"] + "/files/p0.jpg", "turn": 0},
             {"page": 2, "url": site["base"] + "/files/p2.jpg", "turn": 90}]
    out = run_reread("song-4", pages, [{"keep": 0}], {"bars": []}, again, site["base"] + "/api/conversion/reread",
                     SECRET, allow_plain_http=True, report_waits=(0,), page_url=site["base"] + "/api/conversion/page")
    assert seen["pictures"] == {0: b"PAGE:/files/p0.jpg", 2: b"PAGE:/files/p2.jpg"} and seen["turns"] == {2: 90}
    assert [(p["path"], p["body"]) for p in site["pages"]] == [("/api/conversion/page?song=song-4&page=2", b"TURNED")]
    body = site["calls"][0]["body"]
    assert out["reported"] and body["ok"] and body["bar_map"] == {"1": 0} and body["turned"] == [2] and body["song_id"] == "song-4"

    # a turned page that cannot be stored means the old notes must stay
    site["calls"].clear()

    def broken(pictures, turns, lines, old):
        return dict(again(pictures, turns, lines, old), images={2: b"BROKEN"})

    run_reread("song-4", pages, [{"keep": 0}], {"bars": []}, broken, site["base"] + "/api/conversion/reread",
               SECRET, allow_plain_http=True, report_waits=(0,), page_url=site["base"] + "/api/conversion/page")
    assert site["calls"][0]["body"]["ok"] is False and "score" not in site["calls"][0]["body"]


# ------------------------------------------------------------------ with the real engine

@pytest.mark.slow
def test_the_sample_page_line_by_line_and_upside_down():
    pytest.importorskip("homr")
    from conversion import convert
    from tests.sample.expected import problems

    with open(SAMPLE, "rb") as handle:
        whole = convert([handle.read()], with_title=False, keep_page_images=True)
    old, page = whole["score"], whole["images"][0]
    first_box, second_box = old["lines"][0]["box"], old["lines"][1]["box"]

    # Read the second line again from a slightly larger box: same notes as before.
    wider = [max(0, second_box[0] - 0.01), second_box[1] - 0.01, min(1, second_box[2] + 0.01), min(1, second_box[3] + 0.01)]
    out = reread({0: page}, {}, [{"keep": 0}, {"page": 0, "box": wider}], old)
    assert problems(out["score"]) == [] and out["bar_map"] == {"0": 0, "1": 1, "2": 2, "3": 3}
    assert len(out["score"]["lines"][1]["barx"]) == 5

    # The same page stored upside down: turn it, box both lines, read both.
    upside = cv2.imencode(".jpg", cv2.rotate(cv2.imdecode(np.frombuffer(page, np.uint8), cv2.IMREAD_COLOR), cv2.ROTATE_180))[1].tobytes()
    out = reread({0: upside}, {0: 180}, [{"page": 0, "box": first_box}, {"page": 0, "box": second_box}], old)
    assert problems(out["score"]) == [] and len(out["score"]["bars"]) == 8 and out["read"] == 2
