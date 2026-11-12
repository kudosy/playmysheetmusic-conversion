"""Convert files on your own computer, without Modal.

    python scripts/convert_file.py page1.jpg page2.jpg -o song.json
    python scripts/convert_file.py score.pdf --pages 1 --top 0.33

All files given together are read as the pages of one song.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conversion import ConversionError, convert  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", help="PDF or image files, in page order")
    parser.add_argument("-o", "--output", help="where to write the result (JSON); default: print a summary only")
    parser.add_argument("--pages", type=int, default=None, help="read at most this many pages")
    parser.add_argument("--top", type=float, default=None, help="read only this share of each page, from the top (e.g. 0.33)")
    parser.add_argument("--musicxml", action="store_true", help="include homr's MusicXML in the result")
    args = parser.parse_args()

    blobs = []
    for path in args.files:
        with open(path, "rb") as handle:
            blobs.append(handle.read())
    try:
        result = convert(blobs, max_pages=args.pages, top_fraction=args.top, keep_musicxml=args.musicxml)
    except ConversionError as error:
        print(f"Could not convert ({error.code}): {error.message}", file=sys.stderr)
        return 1

    score = result["score"]
    print(f"Title read from the page: {result['title'] or '(none)'}")
    print(f"{len(score['bars'])} bars, {len(score['notes'])} notes, {result['seconds']} seconds")
    for page in result["pages"]:
        steps = ", ".join(page["steps"]) or "no clean-up needed"
        found = f"{page['bars']} bars, {page['notes']} notes" if page["ok"] else "no music found"
        print(f"  page {page['page'] + 1}: {found} in {page['seconds']}s ({steps})")
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(result, handle, separators=(",", ":"))
        print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
