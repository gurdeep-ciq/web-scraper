"""Walmart feasibility spike (mirrors Total Wine's probe_network).

Walmart uses PerimeterX (appId PXu6b0qd2S) like Total Wine, but its sitemaps are
PX-gated too, so we fetch them THROUGH the warmed browser. Steps:
  1. Warm PX on the homepage (patchright + real Chrome).
  2. Fetch product sitemaps via the browser -> collect /ip/ product URLs.
  3. Load one product page, capture its JSON API calls + __NEXT_DATA__ to find
     where name / price / reviews live.

    python -m scraper.walmart_probe
"""

from __future__ import annotations

import json
import pathlib
import re
import time

OUT = pathlib.Path("spike_out/walmart")
PROFILE = pathlib.Path("spike_out/walmart_profile")
HOME = "https://www.walmart.com/"
# Child sitemaps (one level below the indexes) actually contain /ip/ URLs.
# hi_ip children are NOT gzipped, so the browser renders them directly.
SITEMAPS = [
    "https://www.walmart.com/sitemap_hi_ip_1.xml",
    "https://www.walmart.com/sitemap_hi_ip_2.xml",
    "https://www.walmart.com/sitemap_hi_ip_3.xml",
]
INTERESTING = ("graphql", "orchestra", "product", "price", "review", "item", "terra")
# NB: the PX app-id appears in every page's sensor script, so it is NOT a block
# signal. A real block is the interstitial / captcha / /blocked redirect.
BLOCK_MARKERS = ("px-captcha", "Robot or human", "Access to this page has been denied",
                 "/blocked?url=", "Verify your identity", "Activate and hold")


def _blocked(html: str) -> bool:
    return any(m in html for m in BLOCK_MARKERS)


def main() -> None:
    from patchright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), channel="chrome", headless=False, no_viewport=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # 1. warm PX on homepage
        page.goto(HOME, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(9000)
        home_blocked = _blocked(page.content())
        print(f"[1] homepage blocked by PX: {home_blocked}")

        # 2. fetch product sitemaps through the browser
        product_urls: list[str] = []
        for sm in SITEMAPS:
            try:
                page.goto(sm, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(2500)
                body = page.content()
            except Exception as e:  # noqa: BLE001
                print(f"[2] {sm} -> ERROR {e}")
                continue
            blocked = _blocked(body)
            locs = re.findall(r"https://www\.walmart\.com/ip/[^<\s\"]+", body)
            (OUT / f"sitemap_{sm.rsplit('/',1)[-1]}.txt").write_text(body[:200000], errors="replace")
            print(f"[2] {sm.rsplit('/',1)[-1]:24} blocked={blocked} ip_urls={len(locs)}")
            product_urls += locs
            if len(product_urls) >= 5:
                break

        if not product_urls:
            print("[2] no product URLs obtained (sitemaps blocked?) — stopping")
            input("Press Enter to close...")
            ctx.close()
            return

        # 3. load a product page and capture its data
        cap = []
        saved = {"n": 0}

        def on_response(resp):
            u = resp.url
            ct = ""
            try:
                ct = resp.headers.get("content-type", "")
            except Exception:
                pass
            if "json" in ct or "graphql" in u:
                cap.append(f"{resp.status} {u[:130]}")
                if any(k in u.lower() for k in INTERESTING):
                    try:
                        body = resp.text()
                    except Exception:
                        return
                    saved["n"] += 1
                    (OUT / f"resp_{saved['n']:03d}.json").write_text(
                        json.dumps({"url": u}) + "\n" + body[:400000], errors="replace")

        page.on("response", on_response)
        purl = product_urls[0]
        print(f"\n[3] loading product: {purl}")
        page.goto(purl, wait_until="domcontentloaded", timeout=60_000)
        for _ in range(6):
            page.mouse.wheel(0, 1400)
            page.wait_for_timeout(2500)
        html = page.content()
        (OUT / "product.html").write_text(html, errors="replace")
        (OUT / "network_log.txt").write_text("\n".join(cap), errors="replace")

        prod_blocked = _blocked(html)
        print(f"[3] product blocked by PX: {prod_blocked} | json responses: {len(cap)} | saved: {saved['n']}")

        # __NEXT_DATA__ presence + a peek at product fields
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if m:
            (OUT / "next_data.json").write_text(m.group(1)[:1_000_000], errors="replace")
            print("[3] __NEXT_DATA__ captured ->", len(m.group(1)), "bytes")
            for key in ('"price"', '"priceInfo"', '"name"', '"averageRating"', '"numberOfReviews"'):
                print(f"     contains {key}: {key in m.group(1)}")
        else:
            print("[3] no __NEXT_DATA__ found")

        print("\ninteresting endpoints:")
        for ln in cap:
            if any(k in ln.lower() for k in INTERESTING):
                print("  " + ln)
        print(f"\nSaved to {OUT}/")
        input("Press Enter to close...")
        ctx.close()


if __name__ == "__main__":
    main()
