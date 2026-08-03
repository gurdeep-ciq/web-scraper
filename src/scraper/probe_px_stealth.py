"""PerimeterX probe, stealth edition (uses patchright + real Chrome).

Vanilla Playwright is detected by PX (CDP `Runtime.enable` leak) so the
Press & Hold challenge becomes unsolvable. `patchright` is a drop-in patched
Playwright that removes that leak; combined with real Google Chrome
(channel="chrome") and a persistent profile, PX's invisible check often passes
outright — and when Press & Hold does appear, it can actually be completed.

Deliberately minimal: NO extra automation flags, NO init scripts. patchright's
stealth is undone by the manual hacks we used in probe_px.py, so they're gone.

Setup:
    pip install patchright
    patchright install chromium      # or rely on channel="chrome" below

Run headed:
    python -m scraper.probe_px_stealth
    python -m scraper.probe_px_stealth --channel chromium   # if no Chrome
    python -m scraper.probe_px_stealth --url <product-url> --wait 180
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import time

OUT = pathlib.Path("spike_out")
PROFILE_DIR = OUT / "px_profile_stealth"
DEFAULT_URL = (
    "https://www.totalwine.com/spirits/bourbon/"
    "jim-beam-kentucky-fire-bourbon-whiskey/p/140521750"
)


def _looks_blocked(html: str) -> bool:
    return (
        '"appId": "PXFF0j69T5"' in html
        or "Access to this page has been denied" in html
        or "Press & Hold" in html
        or "px-captcha" in html
    )


def _report_data(html: str) -> None:
    ld = re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S
    )
    print(f"JSON-LD blocks found: {len(ld)}")
    for block in ld:
        try:
            data = json.loads(block)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("@type") in ("Product", "IndividualProduct"):
            offers = data.get("offers", {})
            rating = data.get("aggregateRating") or {}
            print("  name:", data.get("name"))
            print("  price:", offers.get("price") if isinstance(offers, dict) else offers)
            print("  rating:", rating.get("ratingValue"))
            print("  reviewCount:", rating.get("reviewCount"))
    for tok in ("__NEXT_DATA__", "window.__", "productData", "digitalData"):
        if tok in html:
            print(f"  embedded-state marker present: {tok}")


def main() -> None:
    ap = argparse.ArgumentParser(description="PerimeterX stealth probe (patchright)")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--channel", default="chrome", help="chrome (real) or chromium")
    ap.add_argument("--wait", type=int, default=180)
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    PROFILE_DIR.mkdir(exist_ok=True)

    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("patchright not installed. Run: pip install patchright")

    with sync_playwright() as p:
        # patchright best practice: persistent context, real chrome, no extra
        # flags / init scripts (those re-introduce detectable tells).
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel=args.channel,
            headless=False,
            no_viewport=True,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(args.url, wait_until="domcontentloaded", timeout=60_000)
        print("\n>>> If a 'Press & Hold' challenge appears, complete it now.")
        print(f">>> Waiting up to {args.wait}s for the page to clear...\n")

        deadline = time.time() + args.wait
        html = page.content()
        while time.time() < deadline:
            html = page.content()
            if not _looks_blocked(html):
                print("PX CLEARED")
                break
            time.sleep(2)
        else:
            print("Still blocked when wait expired.")

        (OUT / "px_stealth_product.html").write_text(html, encoding="utf-8", errors="replace")
        cookies = context.cookies()
        (OUT / "px_stealth_cookies.json").write_text(json.dumps(cookies, indent=2))

        blocked = _looks_blocked(html)
        print(f"\nRESULT: {'BLOCKED by PX' if blocked else 'PX CLEARED'}  (bytes={len(html)})")
        print("has _px3 cookie:", any(c["name"] == "_px3" for c in cookies))
        if not blocked:
            _report_data(html)

        print(f"\nSaved: {OUT}/px_stealth_product.html + px_stealth_cookies.json")
        input("\nPress Enter to close the browser...")
        context.close()


if __name__ == "__main__":
    main()
