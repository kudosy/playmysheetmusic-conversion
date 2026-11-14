# Last deploy: success

The live service was checked with the sample page and every check passed.

- Commit: 2994d7f0dbf82da6f1c1e51270d05b957916fbdf
- Finished: 2026-11-14 17:57 UTC
- Run: https://github.com/kudosy/playmysheetmusic-conversion/actions/runs/43623769856

```
== Quick tests ==
.............................sss                                         [100%]
29 passed, 3 skipped in 4.41s
== Shared secret ==
Secret pms-conversion already exists; left as it is.
== Deploy ==
✓ Created objects.
├── ✓ Uploaded 
│   /home/runner/work/playmysheetmusic-conversion/playmysheetmusic-conversion/mo
│   dal_app.py
├── ✓ Uploaded PythonPackage:conversion
├── ✓ Created Function _download_models.
├── ✓ Created Function read_one_page.
├── ✓ Created Function convert_song.
├── ✓ Created Function convert_purchased_song.
├── ✓ Created Function reread_song.
└── ✓ Created Function web
    └── https://gamifyparenting--pms-conversion-web.us-east.modal.run 
        (unauthenticated)
✓ App deployed in 1.833s! 🎉
View Deployment: 
https://modal.com/apps/gamifyparenting/main/deployed/pms-conversion
== Live check ==
PASS  one page: every note right
PASS  lines and bars are located on the page  ([{'page': 0, 'line': 0, 'first': 0, 'last': 3, 'box': [0.0, 0.0157, 1.0, 0.5026], 'barx': [0.0973, 0.3126, 0.5217, 0.7389, 0.9739]}, {'page': 0, 'line': 1, 'first': 4, 'last': 7, 'box': [0.0044, 0.502)
      first request after deploy (cold start): 7.2s, of which reading the page 4.3s
PASS  same page again, service awake
      second request (warm): 3.2s, of which reading the page 3.0s
      wake-up delay: about 4s
PASS  two pages read side by side make one song
      two pages: 10.4s
PASS  top half of the page only (preview)  (4 bars)
PASS  a file that is not a picture is refused  ({'ok': False, 'code': 'unreadable', 'message': "We couldn't open this file. Please upload a PDF or a photo."})
PASS  a purchased song is fetched from its link and converted
PASS  checkout preview: top of the page from a link  (4 bars)
      preview: 3.0s
      web address: https://gamifyparenting--pms-conversion-web.us-east.modal.run
PASS  web address answers /health  (200 {"ok":true,"service":"pms-conversion"})
PASS  web address refuses /convert without the secret  (401)
PASS  web address refuses /jobs without the secret  (401)
PASS  web address refuses /songs without the secret  (401)
PASS  web address refuses /preview without the secret  (401)
ALL CHECKS PASSED
```
