"""What is printed on sample_tune.png (see make_sample.py): the MIDI number of
every note, bar by bar, for each hand."""

RIGHT = [[67, 69, 71, 72], [74, 74, 71], [72, 72, 69], [71, 69, 67],
         [67, 71, 74, 71], [72, 69, 66], [67, 69, 71, 69], [67]]
LEFT = [[55, 50], [47, 55], [48, 50], [55], [55, 50], [48, 50], [52, 50], [43]]


def problems(score: dict, repeats: int = 1) -> list[str]:
    """Compare a conversion result with the page. An empty list means it is right."""
    found = []
    bars = len(RIGHT) * repeats
    if len(score["bars"]) != bars:
        found.append(f"expected {bars} bars, got {len(score['bars'])}")
        return found
    for hand, name, expected in ((1, "right hand", RIGHT), (2, "left hand", LEFT)):
        for b in range(bars):
            got = [n["m"] for n in sorted(score["notes"], key=lambda n: n["t"]) if n["b"] == b and n["p"] == hand]
            if got != expected[b % len(expected)]:
                found.append(f"bar {b + 1} {name}: expected {expected[b % len(expected)]}, got {got}")
    if any(bar["len"] != 32 for bar in score["bars"]):
        found.append("not every bar is four beats long")
    if any(bar["key"] != 1 for bar in score["bars"]):
        found.append("the key signature (one sharp) was not kept on every line")
    return found
