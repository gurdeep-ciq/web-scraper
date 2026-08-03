"""Confirm the production primitive: navigate + intercept the SPA's own JSON.

PerimeterX (first-party mode) signs the page's own XHRs, so hand-issued API
calls 403. The reliable path is to load the product page like a human and
capture the getProduct / reviews / summary responses the app fires itself.

This visits a few DIFFERENT products from the sitemap, intercepts those
responses, and prints the parsed fields. Stable 200s here == POC is feasible
end to end, and this file is the blueprint for the real fetch layer.

    python -m scraper.probe_intercept
    python -m scraper.probe_intercept --n 5
"""

from __future__ import annotations

import argparse
import pathlib
import re
import time

PROFILE_DIR = pathlib.Path("spike_out/px_profile_stealth")
SITEMAP = pathlib.Path("spike_out/recon_product_sitemap_0.txt")

FALLBACK_URLS = [
    "https://www.totalwine.com/spirits/bourbon/jim-beam-kentucky-fire-bourbon-whiskey/p/140521750",
]


def product_urls(n: int) -> list[str]:
    if SITEMAP.exists():
        locs = re.findall(r"<loc>(https://www\.totalwine\.com/[^<]+/p/\d+)</loc>",
                          SITEMAP.read_text(errors="replace"))
        # spread across the file so we hit varied departments
        if locs:
            step = max(1, len(locs) // n)
            return locs[::step][:n]
    return FALLBACK_URLS[:n]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3)
    args = ap.parse_args()

    from patchright.sync_api import sync_playwright

    urls = product_urls(args.n)
    print(f"Visiting {len(urls)} products...\n")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            no_viewport=True,
        )
        page = context.pages[0] if context.pages else context.new_page()

        captured: dict = {}

        def on_response(resp):
            u = resp.url
            if "/getProduct/" in u and resp.status == 200:
                captured["product"] = resp
            elif "/product-reviews/v1/products/" in u and "/reviews?" in u and resp.status == 200:
                captured["reviews"] = resp
            elif "/reviews/summary" in u and resp.status == 200:
                captured["summary"] = resp

        page.on("response", on_response)

        ok = 0
        for i, url in enumerate(urls):
            captured.clear()
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(6000)
            prod = captured.get("product")
            if not prod:
                print(f"[{i}] NO getProduct captured (blocked?) {url}")
                continue
            try:
                d = prod.json()
            except Exception:
                print(f"[{i}] getProduct not JSON")
                continue
            ok += 1
            price = (d.get("price") or [{}])[0].get("price")
            revs = captured.get("reviews")
            rev_total = revs.json().get("totalResults") if revs else None
            summ = captured.get("summary")
            summ_txt = (summ.json().get("summary") if summ else "") or ""
            print(f"[{i}] {d.get('name')}")
            print(f"     sku={d.get('skuId')} price={price} "
                  f"rating={d.get('customerAverageRating')} "
                  f"reviews={d.get('customerReviewsCount')} (api totalResults={rev_total})")
            print(f"     ai_summary={'yes ('+str(len(summ_txt))+' chars)' if summ_txt else 'none'}")
            time.sleep(3)  # polite pacing between products

        print(f"\n{ok}/{len(urls)} products captured successfully.")
        input("Press Enter to close...")
        context.close()


if __name__ == "__main__":
    main()
