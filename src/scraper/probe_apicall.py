"""Find the exact primitive that makes getProduct return 200.

Finding so far: reviews API works via context.request after any warm-up, but
getProduct 403s unless the request looks like it came from the product page
(PerimeterX first-party signals + Referer/origin). This probe navigates to the
real product page, then tries getProduct three ways and reports which wins:

  A) context.request.get (no referer)      -- baseline (expected 403)
  B) context.request.get WITH Referer      -- cheap fix
  C) in-page fetch() via page.evaluate     -- request originates from the page

Whichever returns 200 is the production fetch primitive.

    python -m scraper.probe_apicall
"""

from __future__ import annotations

import pathlib

PROFILE_DIR = pathlib.Path("spike_out/px_profile_stealth")
SKU = "140521750-1"
STORE = "303"
STATE = "US-NJ"
PRODUCT_PAGE = (
    "https://www.totalwine.com/spirits/bourbon/"
    "jim-beam-kentucky-fire-bourbon-whiskey/p/140521750"
)
GETPRODUCT = (
    f"https://www.totalwine.com/product/api/product/product-detail/v1/getProduct/"
    f"{SKU}?shoppingMethod=INSTORE_PICKUP&state={STATE}&attrConfig=true&storeId={STORE}"
)


def main() -> None:
    from patchright.sync_api import sync_playwright

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            no_viewport=True,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(PRODUCT_PAGE, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(6000)  # let PX sensor + SPA settle

        # A) context.request, no referer
        a = context.request.get(GETPRODUCT)
        print(f"A context.request (no referer): {a.status}")

        # B) context.request WITH referer/origin
        b = context.request.get(
            GETPRODUCT,
            headers={"referer": PRODUCT_PAGE, "origin": "https://www.totalwine.com"},
        )
        print(f"B context.request (+referer):   {b.status}")

        # C) in-page fetch — originates from the product page document
        c = page.evaluate(
            """async (url) => {
                const r = await fetch(url, {credentials: 'include'});
                const t = await r.text();
                return {status: r.status, body: t.slice(0, 400)};
            }""",
            GETPRODUCT,
        )
        print(f"C in-page fetch():              {c['status']}")

        for label, resp in (("B", b), ):
            if resp.status == 200:
                d = resp.json()
                print(f"\n[{label}] name={d.get('name')} "
                      f"price={(d.get('price') or [{}])[0].get('price')} "
                      f"rating={d.get('customerAverageRating')} "
                      f"reviews={d.get('customerReviewsCount')}")
        if c["status"] == 200:
            print("\n[C] body head:", c["body"][:200])

        input("\nPress Enter to close...")
        context.close()


if __name__ == "__main__":
    main()
