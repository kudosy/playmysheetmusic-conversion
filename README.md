# playmysheetmusic-conversion

The conversion service behind [PlayMySheetMusic.com](https://playmysheetmusic.com):
a PDF or photo of printed sheet music goes in, and the notes come out in the
form the site's player and editor read.

It is a separate, public repository because the recognition engine,
[homr](https://github.com/liebharc/homr), is licensed AGPL-3.0. This code is
released under the same licence (see `LICENSE`).

## What it does

1. **Opens the file.** PDF, JPG, PNG, HEIC, WebP or TIFF. A PDF becomes one
   picture per page.
2. **Cleans up each page.** Turns a sideways or upside-down page the right way
   up, straightens a tilted one, and evens out shadows. No AI upscaling.
3. **Reads the music** with homr, which writes MusicXML.
4. **Converts the MusicXML to notes** for the player, joining all pages into
   one song. It also repairs one thing homr often gets wrong on photos: the
   key signature being missed on some lines.

## What's here

| Path | What it is |
|---|---|
| `conversion/normalize.py` | Step 1 and 2: opening files and cleaning up pages. |
| `conversion/recognize.py` | Step 3: running homr on one page. |
| `conversion/notes.py` | Step 4: MusicXML to the note model. No dependencies. |
| `conversion/pipeline.py` | All four steps together: `convert(files)`. |
| `conversion/jobs.py` | After a purchase: fetch a song's pages, convert, report back to the website. |
| `conversion/webapi.py` | The web address (FastAPI). |
| `modal_app.py` | How the service runs on [Modal](https://modal.com). |
| `scripts/convert_file.py` | Convert a file on your own computer. |
| `scripts/smoke_test.py` | Checks the live service after each deploy. |
| `tests/` | Automated tests, with a small sample page. |
| `.github/workflows/deploy.yml` | Tests, deploys and checks on every push to `main`. |

## The result

```json
{
  "ok": true,
  "title": "Israel, Israel, God Is Calling",
  "score": {
    "bars":  [{"n": 0, "start": 0, "len": 32, "voice": false, "page": 0, "line": 0, "key": -1}],
    "notes": [{"id": "0R0.0.0", "b": 0, "p": 1, "v": 0, "t": 0, "d": 12, "s": 31, "a": 0, "m": 65}]
  },
  "pages": [{"page": 0, "ok": true, "bars": 16, "notes": 239, "seconds": 25.2,
             "steps": ["straightened by -0.95 degrees", "evened out the lighting"]}],
  "seconds": 26.3
}
```

`score` is exactly what goes into `songs.notes` on the site. Time is counted
in 32nd notes. In a note, `b` is the bar, `p` the part (0 voice line, 1 right
hand, 2 left hand), `v` the voice within the part, `t` the start inside the
bar, `d` the length, `s` the diatonic step (octave x 7 + letter, C = 0), `a`
the sharp or flat and `m` the MIDI number. In a bar, `page` and `line` say
where it is printed and `key` is the key signature (sharps, or flats when
negative).

When a file cannot be used the result is `{"ok": false, "code": ..., "message": ...}`
with code `no_music` ("We couldn't find sheet music in this image.") or
`unreadable`.

## The web address

Everything except `/health` needs the header
`Authorization: Bearer <CONVERSION_SECRET>`.

| Request | What it does |
|---|---|
| `GET /health` | Is the service up? |
| `POST /convert` | Send file(s), wait, get the result. Best for one page or a preview. |
| `POST /jobs` | Send file(s), get `{"job": "..."}` back at once. Best for long songs. |
| `GET /jobs/<job>` | `{"status": "working"}` until finished, then the result. |
| `POST /songs` | What the website uses after a purchase. Send links to each song's pages and an address to call back; the answer comes at once and each result is posted to that address when ready. |

Files go in a form field called `files`; repeat it for several photos of one
song, in page order. Optional fields: `pages` (read at most this many pages),
`top` (read only this share of each page from the top, for example `0.33`),
`musicxml` (`true` to get homr's MusicXML back as well).

```
curl -H "Authorization: Bearer $CONVERSION_SECRET" \
     -F files=@page1.jpg -F files=@page2.jpg \
     https://<workspace>--pms-conversion-web.modal.run/convert
```

`/songs` takes JSON rather than files:

```json
{"callback_url": "https://playmysheetmusic.com/api/conversion/callback",
 "songs": [{"song_id": "…", "files": ["https://…/page1.jpg", "https://…/page2.jpg"]}]}
```

For each song the service downloads the pages, converts them and posts
`{"song_id": …, "ok": true, "score": …}` (or `"ok": false` with a `code` and
`message`) to `callback_url`, with the same `Authorization` header so the
website knows who is calling. It tries a few times if the website is busy.

The shared secret lives in a Modal secret named `pms-conversion`, key
`CONVERSION_SECRET`. The first deploy creates it with a random value, so the
address is locked until someone sets a real one.

## Running it on your own computer

Python 3.11 or newer.

```
pip install -r requirements.txt
python scripts/convert_file.py my_score.pdf -o my_score.json
```

The first run downloads homr's models (about 300 MB). A page takes roughly
half a minute on an ordinary processor.

## Tests

```
pip install pytest httpx "fastapi[standard]"
python -m pytest tests
```

`tests/sample/sample_tune.png` is a short tune written for this repository
(`tests/sample/make_sample.py` draws it), and the tests check every note read
from it.

## Deploying

Pushing to `main` runs `.github/workflows/deploy.yml`, which needs two
repository secrets: `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET`. After
deploying, it sends the sample page to the live service and checks the
answer. The outcome and log are written to `STATUS.md` on the `deploy-log`
branch.

## Known limits

- Printed music only. No handwriting, guitar tab or chord charts.
- Grace notes and ornaments (trills and so on) are left out.
- Repeats are not unfolded; the music is played once through as printed.
- A piece that changes to a key with no sharps or flats exactly at the start
  of a line, with no warning at the end of the line before, keeps its old key.
