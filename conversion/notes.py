"""Turn MusicXML (one file per page, as homr writes it) into the note model the
PlayMySheetMusic player and editor read.

    {
      "bars":  [{"n", "start", "len", "voice", "page", "line"}],
      "notes": [{"id", "b", "p", "v", "t", "d", "s", "a", "m"}]
    }

Time is counted in 32nd notes. `start` is where a bar begins in the song, `t`
where a note begins inside its bar and `d` how long it lasts. `p` is the part:
0 voice line, 1 right hand, 2 left hand. `s` is the diatonic step
(octave * 7 + letter, C = 0), `a` the alteration in semitones and `m` the MIDI
number. A note id starts with its bar number, which the player relies on.

Only the Python standard library is used here.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction

UNITS_PER_QUARTER = 8  # 32nd notes
STEP_INDEX = {"C": 0, "D": 1, "E": 2, "F": 3, "G": 4, "A": 5, "B": 6}
SEMITONES = [0, 2, 4, 5, 7, 9, 11]
PART_LETTER = {0: "V", 1: "R", 2: "L"}
DEFAULT_BAR_LEN = Fraction(32)  # 4/4, used only when nothing better is known


def midi_number(step: int, alter: int) -> int:
    return 12 * (step // 7 + 1) + SEMITONES[step % 7] + alter


@dataclass
class _Note:
    onset: Fraction  # inside the bar, 32nds
    dur: Fraction
    step: int
    alter: int
    voice: str
    staff: int
    tie_start: bool = False
    tie_stop: bool = False


@dataclass
class _Measure:
    notes: list[_Note] = field(default_factory=list)
    extent: Fraction = Fraction(0)  # furthest point any voice reached
    filler: bool = True  # nothing but a whole-bar rest, or empty
    time: Fraction | None = None  # bar length from a time signature set here
    key: int | None = None  # key signature (sharps, or flats when negative) set here
    new_system: bool = False


@dataclass
class _Part:
    staves: int = 1
    clefs: dict[int, str] = field(default_factory=dict)
    measures: list[_Measure] = field(default_factory=list)


def _num(value: Fraction) -> int | float:
    """Whole numbers stay whole; triplets and the like keep three decimals."""
    if value.denominator == 1:
        return int(value)
    return round(float(value), 3)


def _text_int(element: ET.Element | None, default: int = 0) -> int:
    if element is None or element.text is None:
        return default
    try:
        return int(round(float(element.text)))
    except ValueError:
        return default


def _parse_part(part: ET.Element) -> _Part:
    out = _Part()
    divisions = 1
    for measure in part.findall("measure"):
        m = _Measure()
        cursor = Fraction(0)
        last_onset = Fraction(0)

        def units(element: ET.Element | None) -> Fraction:
            return Fraction(_text_int(element) * UNITS_PER_QUARTER, divisions)

        for child in measure:
            tag = child.tag
            if tag == "print":
                if child.get("new-system") == "yes" or child.get("new-page") == "yes":
                    m.new_system = True
            elif tag == "attributes":
                div = child.find("divisions")
                if div is not None and _text_int(div) > 0:
                    divisions = _text_int(div)
                staves = child.find("staves")
                if staves is not None:
                    out.staves = max(out.staves, _text_int(staves, 1))
                for clef in child.findall("clef"):
                    number = int(clef.get("number", "1"))
                    out.clefs.setdefault(number, (clef.findtext("sign") or "G").upper())
                key = child.find("key")
                if key is not None and key.find("fifths") is not None:
                    m.key = max(-7, min(7, _text_int(key.find("fifths"))))
                time = child.find("time")
                if time is not None:
                    beats = _text_int(time.find("beats"))
                    beat_type = _text_int(time.find("beat-type"))
                    if beats > 0 and beat_type > 0:
                        m.time = Fraction(beats * 4 * UNITS_PER_QUARTER, beat_type)
            elif tag == "backup":
                cursor = max(Fraction(0), cursor - units(child.find("duration")))
            elif tag == "forward":
                cursor += units(child.find("duration"))
                m.extent = max(m.extent, cursor)
            elif tag == "note":
                if child.find("grace") is not None:
                    continue  # ornaments carry no time of their own
                dur = units(child.find("duration"))
                is_chord = child.find("chord") is not None
                onset = last_onset if is_chord else cursor
                rest = child.find("rest")
                if rest is not None:
                    if rest.get("measure") != "yes":
                        m.filler = False
                        m.extent = max(m.extent, onset + dur)
                else:
                    pitch = child.find("pitch")
                    if pitch is not None and dur > 0:
                        letter = (pitch.findtext("step") or "C").upper()
                        step = _text_int(pitch.find("octave"), 4) * 7 + STEP_INDEX.get(letter, 0)
                        ties = {t.get("type") for t in child.findall("tie")}
                        m.notes.append(
                            _Note(
                                onset=onset,
                                dur=dur,
                                step=step,
                                alter=_text_int(pitch.find("alter")),
                                voice=child.findtext("voice") or "1",
                                staff=_text_int(child.find("staff"), 1),
                                tie_start="start" in ties,
                                tie_stop="stop" in ties,
                            )
                        )
                        m.filler = False
                        m.extent = max(m.extent, onset + dur)
                if not is_chord:
                    last_onset = cursor
                    cursor += dur
        out.measures.append(m)
    return out


# ---------------------------------------------------------------- key signatures
#
# homr reads the key signature afresh at the start of every line of music and
# sometimes gets it wrong on a line (most often it misses it). Every note on
# that line then comes out without the sharps or flats of the key. A key
# signature is printed the same on every line, so we work out the one the
# piece is really in and put the missing sharps and flats back.

_SHARP_ORDER = [3, 0, 4, 1, 5, 2, 6]  # F C G D A E B, as letter numbers
_FLAT_ORDER = [6, 2, 5, 1, 4, 0, 3]  # B E A D G C F


def key_alter(fifths: int, letter: int) -> int:
    """What a key signature does to a letter: +1 sharp, -1 flat, 0 nothing."""
    if fifths > 0 and letter in _SHARP_ORDER[:fifths]:
        return 1
    if fifths < 0 and letter in _FLAT_ORDER[:-fifths]:
        return -1
    return 0


def _tonic_letters(fifths: int) -> set[int]:
    """Letters of the major key and the minor key that share a key signature."""
    major = (fifths * 4) % 7
    return {major, (major + 5) % 7}


def _choose_key(readings: list[int], first_bass: int | None, last_bass: int | None) -> int:
    """Pick the key signature of a stretch of music from what was read on each
    of its lines. Music nearly always ends, and usually begins, on its home
    note, so that decides between readings; otherwise the most common wins."""
    counts = Counter(readings)
    if len(counts) == 1:
        return readings[0]
    for bass in (last_bass, first_bass):
        if bass is None:
            continue
        fits = [k for k in counts if bass in _tonic_letters(k)]
        if len(fits) == 1:
            return fits[0]
    best = max(counts.values())
    for reading in readings:  # earliest line wins a tie
        if counts[reading] == best:
            return reading
    return readings[0]


def _hand_map(parts: list[_Part]) -> list[dict[int, int]]:
    """Decide which part of the song each staff belongs to (0 voice, 1 right
    hand, 2 left hand)."""
    grand = [i for i, p in enumerate(parts) if p.staves >= 2]
    mapping: list[dict[int, int]] = []
    if grand:
        piano = grand[-1]  # the accompaniment is printed lowest
        for i, part in enumerate(parts):
            if i == piano:
                mapping.append({1: 1, 2: 2})
            else:
                mapping.append({s: 0 for s in range(1, part.staves + 1)})
        return mapping
    single = [p for p in parts if p.staves == 1]
    if len(single) == 2 and len(parts) == 2:
        return [{1: 1}, {1: 2}]  # two separate staves read as two hands
    for part in parts:
        mapping.append(
            {s: (2 if part.clefs.get(s, "G") == "F" else 1) for s in range(1, part.staves + 1)}
        )
    return mapping


def _voice_key(voice: str) -> tuple[int, str]:
    return (int(voice), "") if voice.isdigit() else (10**6, voice)


def _page_bar_lengths(
    parts: list[_Part], count: int, inherited: Fraction | None, first_page: bool
) -> tuple[list[Fraction], Fraction | None]:
    """How long each bar on a page lasts."""
    extents: list[Fraction] = []
    declared: list[Fraction | None] = []
    current = inherited
    for i in range(count):
        here = [p.measures[i] for p in parts if i < len(p.measures)]
        for m in here:
            if m.time is not None:
                current = m.time
        declared.append(current)
        extents.append(max((m.extent for m in here), default=Fraction(0)))

    filled = [e for e in extents if e > 0]
    common = Counter(filled).most_common(1)[0] if filled else None

    lengths: list[Fraction] = []
    for i in range(count):
        nominal = declared[i]
        if nominal is None:
            # No time signature on this page: go by what most bars add up to.
            nominal = common[0] if common else DEFAULT_BAR_LEN
        elif common and len(filled) >= 4 and common[0] != nominal and common[1] * 10 >= len(filled) * 7:
            # The printed time signature was misread; the bars themselves agree.
            nominal = common[0]
        lengths.append(nominal)

    # A short opening bar is a pick-up.
    if first_page and count > 1 and 0 < extents[0] < lengths[0]:
        pickup = extents[0]
        lengths[0] = pickup
    return lengths, current


def _resolve_keys(flat: list[dict], raw: list[dict]) -> list[int]:
    """Decide the real key signature of every bar. `flat` has one entry per
    bar with what each part believed; `raw` is every note. Returns the key
    (in fifths) for each bar."""
    count = len(flat)
    if not count:
        return []

    # What was read on each line: the belief of the part with most notes there.
    weight: dict[tuple[int, int], int] = Counter()
    for note in raw:
        weight[(flat[note["b"]]["system"], note["part"])] += 1
    system_bars: dict[int, list[int]] = {}
    for bar in flat:
        system_bars.setdefault(bar["system"], []).append(bar["n"])
    readings: dict[int, int] = {}
    for system, bars in system_bars.items():
        first = flat[bars[0]]
        parts = sorted(first["belief"], key=lambda pi: -weight.get((system, pi), 0))
        readings[system] = first["belief"][parts[0]] if parts else 0

    # A key change printed in the middle of a line is a real change of key.
    starts = [0] + [bar["n"] for bar in flat if bar["mid_line_key"] is not None and bar["n"] > 0]
    starts = sorted(set(starts))

    def bass_letter(bar_number: int, at_end: bool) -> int | None:
        notes = [n for n in raw if n["b"] == bar_number]
        if not notes:
            return None
        if at_end:
            edge = max(n["start"] + n["dur"] for n in notes)
            notes = [n for n in notes if n["start"] + n["dur"] == edge]
        else:
            edge = min(n["start"] for n in notes)
            notes = [n for n in notes if n["start"] == edge]
        return min(notes, key=lambda n: (n["step"], n["alter"]))["step"] % 7

    bars_with_notes = sorted({n["b"] for n in raw})
    result = [0] * count
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else count
        declared = flat[start]["mid_line_key"] if start > 0 else None
        if declared is not None:
            chosen = declared
        else:
            systems = sorted({flat[i]["system"] for i in range(start, end)})
            first_bass = last_bass = None
            inside = [i for i in bars_with_notes if start <= i < end]
            if inside and start == 0:
                # skip a pick-up bar: the first full bar starts on the home chord
                opening = inside[1] if len(inside) > 1 and flat[inside[0]]["pickup"] else inside[0]
                first_bass = bass_letter(opening, at_end=False)
            if inside and end == count and len(systems) > 1:
                last_bass = bass_letter(inside[-1], at_end=True)
            chosen = _choose_key([readings[sy] for sy in systems], first_bass, last_bass)
        for i in range(start, end):
            result[i] = chosen
    return result


def musicxml_to_notes(pages: list[str | bytes]) -> dict:
    """Convert the MusicXML of each page, in order, into one song."""
    flat: list[dict] = []  # one entry per bar of the song
    raw: list[dict] = []  # every note, before tidying
    inherited: Fraction | None = None
    position = Fraction(0)
    system = -1

    for page_index, xml in enumerate(pages):
        root = ET.fromstring(xml)
        parts = [_parse_part(p) for p in root.findall("part")]
        parts = [p for p in parts if p.measures]
        if not parts:
            continue
        mapping = _hand_map(parts)
        count = max(len(p.measures) for p in parts)
        lengths, inherited = _page_bar_lengths(parts, count, inherited, first_page=not flat)
        belief = {pi: 0 for pi in range(len(parts))}  # key each part thinks it is in

        line = 0
        for i in range(count):
            here = [(pi, p.measures[i]) for pi, p in enumerate(parts) if i < len(p.measures)]
            new_line = i == 0 or any(m.new_system for _, m in here)
            if new_line:
                system += 1
                if i > 0:
                    line += 1
            mid_line_key = None
            for pi, m in here:
                if m.key is not None:
                    if not new_line and m.key != belief[pi]:
                        mid_line_key = m.key
                    belief[pi] = m.key
            b = len(flat)
            has_voice = False
            grouped: dict[int, dict[str, list[tuple[int, _Note]]]] = {}
            for pi, m in here:
                if not m.filler and 0 in mapping[pi].values():
                    has_voice = True
                for note in m.notes:
                    hand = mapping[pi].get(note.staff, 1)
                    grouped.setdefault(hand, {}).setdefault(f"{pi}:{note.voice}", []).append((pi, note))
            for hand, voices in grouped.items():
                order = sorted(voices, key=lambda k: (int(k.split(":")[0]), _voice_key(k.split(":")[1])))
                for v, key in enumerate(order):
                    for pi, note in voices[key]:
                        raw.append({"start": position + note.onset, "b": b, "p": hand, "v": v, "part": pi,
                                    "onset": note.onset, "dur": note.dur, "step": note.step, "alter": note.alter,
                                    "tie_start": note.tie_start, "tie_stop": note.tie_stop})
            flat.append({"n": b, "start": position, "len": lengths[i], "voice": has_voice, "page": page_index,
                         "line": line, "system": system, "new_line": new_line, "belief": dict(belief),
                         "mid_line_key": mid_line_key,
                         "pickup": b == 0 and count > 1 and lengths[0] < lengths[1]})
            position += lengths[i]

    # Put back sharps and flats lost where a line's key signature was misread.
    keys = _resolve_keys(flat, raw)
    for note in raw:
        bar = flat[note["b"]]
        believed, real = bar["belief"].get(note["part"], 0), keys[note["b"]]
        if believed == real:
            continue
        letter = note["step"] % 7
        if note["alter"] == key_alter(believed, letter):
            note["alter"] = key_alter(real, letter)
        elif bar["new_line"] and (letter, note["alter"]) in ((0, -1), (3, -1), (2, 1), (6, 1)) \
                and key_alter(real, letter) == 0:
            # C flat, F flat, E sharp or B sharp in the first bar of such a line
            # is the key signature itself, mistaken for an accidental.
            note["alter"] = 0

    # Join tied notes into one longer note, so they are not struck twice.
    raw.sort(key=lambda n: (n["start"], n["p"], n["v"], n["step"]))
    open_ties: dict[tuple[int, int], dict] = {}
    kept: list[dict] = []
    for note in raw:
        note["m"] = midi_number(note["step"], note["alter"])
        key = (note["p"], note["m"])
        held = open_ties.get(key)
        if note["tie_stop"] and held is not None and held["start"] + held["dur"] == note["start"]:
            held["dur"] += note["dur"]
            if not note["tie_start"]:
                del open_ties[key]
            continue
        kept.append(note)
        if note["tie_start"]:
            open_ties[key] = note
        else:
            open_ties.pop(key, None)

    # Number the notes: bar, part letter, voice, position in the voice, position in the chord.
    notes: list[dict] = []
    by_voice: dict[tuple[int, int, int], list[dict]] = {}
    for note in kept:
        by_voice.setdefault((note["b"], note["p"], note["v"]), []).append(note)
    for (b, hand, v) in sorted(by_voice):
        group = sorted(by_voice[(b, hand, v)], key=lambda n: (n["onset"], n["step"], n["alter"]))
        event, chord, last_onset, last_pitch = -1, 0, None, None
        for note in group:
            pitch = (note["onset"], note["step"], note["alter"])
            if pitch == last_pitch:
                # the same note written twice in one chord (two singers on one
                # note): play it once, for the longer of the two lengths
                if _num(note["dur"]) > notes[-1]["d"]:
                    notes[-1]["d"] = _num(note["dur"])
                continue
            last_pitch = pitch
            if note["onset"] != last_onset:
                event, chord, last_onset = event + 1, 0, note["onset"]
            else:
                chord += 1
            notes.append({"id": f"{b}{PART_LETTER[hand]}{v}.{event}.{chord}", "b": b, "p": hand, "v": v,
                          "t": _num(note["onset"]), "d": _num(note["dur"]),
                          "s": note["step"], "a": note["alter"], "m": note["m"]})

    bars = [{"n": bar["n"], "start": _num(bar["start"]), "len": _num(bar["len"]), "voice": bar["voice"],
             "page": bar["page"], "line": bar["line"], "key": keys[bar["n"]]} for bar in flat]
    return {"bars": bars, "notes": notes}


def title_from_musicxml(xml: str | bytes) -> str:
    root = ET.fromstring(xml)
    for path in ("work/work-title", "movement-title", "credit/credit-words"):
        text = root.findtext(path)
        if text and text.strip():
            return text.strip()
    return ""
