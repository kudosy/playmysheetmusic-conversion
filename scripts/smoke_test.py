"""Check the service that is live on Modal. Run by the deploy workflow right
after `modal deploy`; needs MODAL_TOKEN_ID and MODAL_TOKEN_SECRET.

It sends the small sample page and checks every note that comes back, times a
cold start against a warm one, and makes sure the web address is up and locked.
"""

import os
import sys
import time
import urllib.error
import urllib.request

import modal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests.sample.expected import problems  # noqa: E402

APP = "pms-conversion"
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + name + (f"  ({detail})" if detail else ""), flush=True)
    if not ok:
        failures.append(name)


def status_of(url: str, method: str = "GET") -> tuple[int, str]:
    request = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def main() -> int:
    with open(os.path.join(ROOT, "tests", "sample", "sample_tune.png"), "rb") as handle:
        page = handle.read()
    convert = modal.Function.from_name(APP, "convert_song")

    started = time.time()
    first = convert.remote([page])
    cold = time.time() - started
    wrong = problems(first["score"]) if first.get("ok") else [str(first)]
    check("one page: every note right", not wrong, "; ".join(wrong[:3]))
    print(f"      first request after deploy (cold start): {cold:.1f}s, of which reading the page {first.get('seconds')}s", flush=True)

    started = time.time()
    second = convert.remote([page])
    warm = time.time() - started
    check("same page again, service awake", bool(second.get("ok")) and not problems(second["score"]))
    print(f"      second request (warm): {warm:.1f}s, of which reading the page {second.get('seconds')}s", flush=True)
    print(f"      wake-up delay: about {max(0.0, cold - warm):.0f}s", flush=True)

    started = time.time()
    two = convert.remote([page, page])
    wrong = problems(two["score"], repeats=2) if two.get("ok") else [str(two)]
    check("two pages read side by side make one song", not wrong, "; ".join(wrong[:3]))
    print(f"      two pages: {time.time() - started:.1f}s", flush=True)

    preview = convert.remote([page], 1, 0.5)
    check("top half of the page only (preview)", bool(preview.get("ok")) and len(preview["score"]["bars"]) == 4,
          f"{len(preview.get('score', {}).get('bars', []))} bars")

    bad = convert.remote([b"this is not a picture"])
    check("a file that is not a picture is refused", bad.get("ok") is False and bad.get("code") == "unreadable", str(bad)[:120])

    url = modal.Function.from_name(APP, "web").get_web_url()
    print(f"      web address: {url}", flush=True)
    code, body = status_of(url + "/health")
    check("web address answers /health", code == 200 and '"ok":true' in body.replace(" ", ""), f"{code} {body[:80]}")
    code, _ = status_of(url + "/convert", "POST")
    check("web address refuses /convert without the secret", code == 401, str(code))
    code, _ = status_of(url + "/jobs/anything")
    check("web address refuses /jobs without the secret", code == 401, str(code))

    print("\nALL CHECKS PASSED" if not failures else f"\n{len(failures)} CHECK(S) FAILED: " + ", ".join(failures), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
