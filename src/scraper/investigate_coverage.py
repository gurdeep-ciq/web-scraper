"""Diagnose price-coverage properly.

First attempt was misleading: the first N sitemap URLs are clustered size
variants of a few products (many odd sizes no store stocks), and the store
cookie override didn't take. This version:

  - takes a SPREAD sample across the sitemap (every Kth url) so it reflects
    mainstream products, not a cluster of size variants;
  - reports price-fill %, the null reasons, the storeId distribution actually
    returned, and example product names for priced vs unavailable — so we can
    see whether "unavailable" is long-tail junk or real catalog gaps.

    python -m scraper.investigate_coverage --n 40 --step 60

Run headed (reuses the warm PX profile).
"""

from __future__ import annotations

import argparse

from .browser import PXBlocked, TotalWineSession
from .sitemaps import iter_product_urls


def _price(prod: dict):
    p = prod.get("price")
    return p[0].get("price") if isinstance(p, list) and p else None


def _reason(prod: dict) -> str:
    if prod.get("unavailableAtStore") is True:
        return "unavailableAtStore"
    if prod.get("transactional") is False:
        return "not_transactional"
    if not any(o.get("eligible") for o in prod.get("shoppingOptions", [])):
        return "no_eligible_method"
    return "eligible_but_no_price"


def spread_sample(n: int, step: int) -> list[str]:
    """Every `step`-th product URL, so the sample spans many brands/types."""
    urls = list(iter_product_urls(limit=n * step))
    return urls[::step][:n]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--step", type=int, default=60, help="sitemap stride between picks")
    args = ap.parse_args()

    urls = spread_sample(args.n, args.step)
    print(f"Spread sample: {len(urls)} products (every {args.step}th sitemap url)\n")

    priced_names: list[str] = []
    unavail_names: list[str] = []
    reasons: dict[str, int] = {}
    stores: dict[str, int] = {}
    n_ok = 0

    with TotalWineSession() as sess:
        for url in urls:
            try:
                data = sess.fetch(url)
            except PXBlocked:
                continue
            prod = data.get("product")
            if not prod:
                continue
            n_ok += 1
            sid = str(prod.get("storeId"))
            stores[sid] = stores.get(sid, 0) + 1
            name = prod.get("name", "?")
            if _price(prod) is not None:
                priced_names.append(f"{name} = ${_price(prod)}")
            else:
                r = _reason(prod)
                reasons[r] = reasons.get(r, 0) + 1
                if r == "unavailableAtStore":
                    unavail_names.append(name)

    priced = len(priced_names)
    pct = (priced / n_ok * 100) if n_ok else 0
    print(f"PRICE COVERAGE: {priced}/{n_ok} ({pct:.0f}%)")
    print(f"null reasons: {reasons}")
    print(f"storeId distribution: {stores}\n")
    print("--- priced examples ---")
    for s in priced_names[:10]:
        print("  ", s)
    print("--- unavailable examples ---")
    for s in unavail_names[:10]:
        print("  ", s)
    input("\nPress Enter to close...")


if __name__ == "__main__":
    main()
