"""Draw the small test page used by the tests (tests/sample/sample_tune.png).

The tune was written for this repository, so the picture is free to share.
Needs:  pip install verovio cairosvg
"""

import os

import cairosvg
import verovio

HERE = os.path.dirname(os.path.abspath(__file__))

RIGHT = ["4g 4a 4b 4cc", "4dd 4dd 2b", "4cc 4cc 2a", "4b 4a 2g",
         "4g 4b 4dd 4b", "4cc 4a 2f#", "4g 4a 4b 4a", "1g"]
LEFT = ["2G 2D", "2BB 2G", "2C 2D", "1G",
        "2G 2D", "2C 2D", "2E 2D", "1GG"]


def kern() -> str:
    rows = ["**kern\t**kern", "*clefF4\t*clefG2", "*k[f#]\t*k[f#]", "*M4/4\t*M4/4"]
    for number, (right, left) in enumerate(zip(RIGHT, LEFT), start=1):
        rows.append(f"={number}\t={number}")
        r, l = right.split(), left.split()
        # line the two hands up on a grid of quarter notes
        grid_r, grid_l = [], []
        for token in r:
            grid_r += [token] + ["."] * (4 // int(token[0]) - 1)
        for token in l:
            grid_l += [token] + ["."] * (4 // int(token[0]) - 1)
        for a, b in zip(grid_l, grid_r):
            if a != "." or b != ".":
                rows.append(f"{a}\t{b}")
    rows += ["==\t==", "*-\t*-"]
    return "\n".join(rows) + "\n"


def main() -> None:
    toolkit = verovio.toolkit()
    toolkit.setOptions({"pageWidth": 2100, "pageHeight": 1500, "scale": 55, "adjustPageHeight": True,
                        "breaks": "encoded", "footer": "none", "header": "none"})
    data = kern().replace("=5\t=5", "!!linebreak:original\n=5\t=5")
    toolkit.loadData(data)
    svg = toolkit.renderToSVG(1)
    out = os.path.join(HERE, "sample_tune.png")
    cairosvg.svg2png(bytestring=svg.encode(), write_to=out, background_color="white", output_width=1800)
    print("wrote", out)


if __name__ == "__main__":
    main()
