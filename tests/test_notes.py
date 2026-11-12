"""Checks for the MusicXML-to-notes converter. Run with:  python -m pytest tests"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conversion.notes import key_alter, midi_number, musicxml_to_notes  # noqa: E402


def note(step, octave, dur, *, alter=None, chord=False, voice=1, staff=1, tie=None, typ="quarter"):
    return (
        "<note>" + ("<chord/>" if chord else "")
        + f"<pitch><step>{step}</step>" + (f"<alter>{alter}</alter>" if alter is not None else "")
        + f"<octave>{octave}</octave></pitch><duration>{dur}</duration>"
        + (f'<tie type="{tie}"/>' if tie else "")
        + f"<voice>{voice}</voice><type>{typ}</type><staff>{staff}</staff></note>"
    )


def rest(dur, *, voice=1, staff=1, whole_bar=False):
    return f"<note><rest{' measure=\"yes\"' if whole_bar else ''}/><duration>{dur}</duration><voice>{voice}</voice><staff>{staff}</staff></note>"


def backup(dur):
    return f"<backup><duration>{dur}</duration></backup>"


def attributes(divisions=None, staves=None, key=None, time=None, clefs=()):
    out = "<attributes>"
    if divisions:
        out += f"<divisions>{divisions}</divisions>"
    if key is not None:
        out += f"<key><fifths>{key}</fifths></key>"
    if time:
        out += f"<time><beats>{time[0]}</beats><beat-type>{time[1]}</beat-type></time>"
    if staves:
        out += f"<staves>{staves}</staves>"
    for number, sign in clefs:
        out += f'<clef number="{number}"><sign>{sign}</sign></clef>'
    return out + "</attributes>"


def score(*parts):
    body = "".join(
        f'<part id="P{i}">' + "".join(f'<measure number="{n + 1}">{m}</measure>' for n, m in enumerate(measures)) + "</part>"
        for i, measures in enumerate(parts, start=1)
    )
    return f"<score-partwise>{body}</score-partwise>"


NEW_LINE = '<print new-system="yes"/>'
PIANO = dict(divisions=2, staves=2, clefs=((1, "G"), (2, "F")))


def test_pitch_numbers():
    assert midi_number(4 * 7 + 6, 0) == 71  # B4
    assert midi_number(5 * 7 + 0, 1) == 73  # C sharp 5
    assert key_alter(-1, 6) == -1 and key_alter(-1, 2) == 0  # one flat: B only
    assert key_alter(3, 3) == 1 and key_alter(3, 4) == 1 and key_alter(3, 1) == 0  # three sharps: F C G


def test_piano_hands_chords_and_timing():
    xml = score([
        attributes(**PIANO, key=0, time=(4, 4))
        + note("C", 5, 4, typ="half") + note("E", 5, 4, chord=True, typ="half") + note("G", 4, 2) + note("A", 4, 2)
        + backup(8) + note("C", 3, 8, staff=2, voice=5, typ="whole"),
    ])
    out = musicxml_to_notes([xml])
    assert out["bars"] == [{"n": 0, "start": 0, "len": 32, "voice": False, "page": 0, "line": 0, "key": 0}]
    got = {n["id"]: (n["p"], n["t"], n["d"], n["m"]) for n in out["notes"]}
    assert got == {
        "0R0.0.0": (1, 0, 16, 72), "0R0.0.1": (1, 0, 16, 76),
        "0R0.1.0": (1, 16, 8, 67), "0R0.2.0": (1, 24, 8, 69),
        "0L0.0.0": (2, 0, 32, 48),
    }
    assert all(n["id"].startswith(str(n["b"])) for n in out["notes"])  # the player relies on this


def test_voice_line_above_piano_and_pickup_bar():
    voice = [attributes(divisions=1, time=(3, 4), clefs=((1, "G"),)) + rest(3, whole_bar=True),
             note("A", 4, 1) + note("B", 4, 2, typ="half")]
    piano = [attributes(**PIANO, time=(3, 4)) + note("E", 4, 2) + backup(2) + note("A", 2, 2, staff=2, voice=5),
             note("E", 4, 6, typ="half") + backup(6) + note("A", 2, 6, staff=2, voice=5, typ="half")]
    out = musicxml_to_notes([score(voice, piano)])
    assert [(b["start"], b["len"], b["voice"]) for b in out["bars"]] == [(0, 8, False), (8, 24, True)]
    assert sorted({n["p"] for n in out["notes"]}) == [0, 1, 2]
    sung = [n for n in out["notes"] if n["p"] == 0]
    assert [(n["id"], n["t"], n["d"]) for n in sung] == [("1V0.0.0", 0, 8), ("1V0.1.0", 8, 16)]


def test_tied_notes_become_one():
    xml = score([
        attributes(divisions=1, time=(4, 4), clefs=((1, "G"),)) + note("G", 4, 4, tie="start", typ="whole"),
        note("G", 4, 2, tie="stop", typ="half") + note("A", 4, 2, typ="half"),
    ])
    out = musicxml_to_notes([xml])
    assert [(n["b"], n["t"], n["d"], n["m"]) for n in out["notes"]] == [(0, 0, 48, 67), (1, 16, 16, 69)]


def test_misread_key_on_a_later_line_is_repaired():
    # One flat (F major). The second line was read with no key signature, so
    # its B came out natural, and the flat of the signature landed on a C.
    line1 = attributes(divisions=1, key=-1, time=(4, 4), clefs=((1, "G"),)) \
        + note("F", 4, 2, typ="half") + note("B", 4, 2, alter=-1, typ="half")
    line2 = NEW_LINE + attributes(key=0) + note("C", 5, 1, alter=-1) + note("B", 4, 1) \
        + note("F", 4, 1, alter=1) + note("F", 4, 1, alter=1)
    line3 = NEW_LINE + note("B", 4, 2, typ="half") + note("F", 4, 2, typ="half")
    out = musicxml_to_notes([score([line1, line2, line3])])
    assert [b["key"] for b in out["bars"]] == [-1, -1, -1]
    assert [(n["b"], n["m"]) for n in out["notes"]] == [
        (0, 65), (0, 70),  # F, B flat
        (1, 72), (1, 70), (1, 66), (1, 66),  # C natural, B flat again, F sharp kept (a real accidental)
        (2, 70), (2, 65),
    ]


def test_key_change_in_the_middle_of_a_line_is_kept():
    bar1 = attributes(divisions=1, key=2, time=(2, 4), clefs=((1, "G"),)) + note("F", 4, 2, alter=1, typ="half")
    bar2 = attributes(key=0) + note("F", 4, 2, typ="half")
    bar3 = NEW_LINE + note("C", 4, 2, typ="half")
    out = musicxml_to_notes([score([bar1, bar2, bar3])])
    assert [b["key"] for b in out["bars"]] == [2, 0, 0]
    assert [n["m"] for n in out["notes"]] == [66, 65, 60]


def test_pages_join_and_time_signature_carries_over():
    page1 = score([attributes(divisions=1, time=(3, 4), clefs=((1, "G"),)) + note("C", 4, 3, typ="half")])
    page2 = score([attributes(divisions=1, clefs=((1, "G"),)) + note("D", 4, 3, typ="half"),
                    note("E", 4, 3, typ="half")])
    out = musicxml_to_notes([page1, page2])
    assert [(b["n"], b["start"], b["len"], b["page"]) for b in out["bars"]] == [(0, 0, 24, 0), (1, 24, 24, 1), (2, 48, 24, 1)]
    assert [n["id"] for n in out["notes"]] == ["0R0.0.0", "1R0.0.0", "2R0.0.0"]


def test_same_note_twice_in_a_chord_is_played_once():
    xml = score([attributes(divisions=1, time=(4, 4), clefs=((1, "F"),))
                 + note("C", 3, 4, typ="whole") + note("C", 3, 4, chord=True, typ="whole")])
    out = musicxml_to_notes([xml])
    assert len(out["notes"]) == 1 and out["notes"][0]["p"] == 2  # a lone bass staff is the left hand
