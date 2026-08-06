"""Enumerate Walmart beverage-alcohol product ids by paginating the alcohol
browse categories (via the warm session, since browse pages are PX-gated).

The /cp/alcohol landing is only a curated handful; the full grid lives under
/browse/food/976759_2975985 (dept root) and its subcategories, paginated by
?page=N. We read product ids straight from each browse page's HTML.
"""

from __future__ import annotations

import logging
import re
from typing import Iterator

from .browser import PXBlocked

log = logging.getLogger("scraper.walmart.browse")

WM = "https://www.walmart.com"
# Alcohol browse roots (dept + main subcategories) discovered in recon.
# Specific alcohol subcategories first (pure alcohol), then the dept root last
# (the root mixes in non-alcoholic drinks/mixers that the parser filters out).
ALCOHOL_CATEGORIES = [
    "/browse/food/all-whiskey/976759_2975985_8439204_1991671",      # whiskey/spirits
    "/browse/beer/domestic-beer/976759_2975985_5110387_2104158",    # beer
    "/browse/beer/imported-beer/976759_2975985_5110387_3820716",
    "/browse/food/976759_2975985",                                  # Alcohol (all) — last
]
_IP_ID = re.compile(r"/ip/[^\"'\s]*?/(\d{6,})")


def iter_alcohol_product_ids(
    sess, *, limit: int | None = None, max_pages: int = 25
) -> Iterator[str]:
    """Yield distinct Walmart usItemIds for alcohol products.

    Walks each alcohol browse category page by page until a page yields no new
    ids (end of results) or `max_pages`. Dedupes across categories.
    """
    seen: set[str] = set()
    count = 0
    for cat in ALCOHOL_CATEGORIES:
        for page in range(1, max_pages + 1):
            url = f"{WM}{cat}?page={page}"
            try:
                html = sess.load(url)
            except PXBlocked:
                log.warning("browse blocked: %s", url)
                break
            ids = [i for i in dict.fromkeys(_IP_ID.findall(html)) if i not in seen]
            if not ids:
                break  # end of this category's results
            for pid in ids:
                seen.add(pid)
                yield pid
                count += 1
                if limit is not None and count >= limit:
                    return
        log.info("category done: %s (running total %d ids)", cat, len(seen))
