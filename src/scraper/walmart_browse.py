"""Enumerate Walmart beverage-alcohol product ids by paginating the alcohol
browse categories (via the warm session, since browse pages are PX-gated).

Product ids + pagination come from each browse page's server-rendered
__NEXT_DATA__ at props.pageProps.initialData.searchResult:
  - itemStacks[].items[].usItemId  -> product ids
  - paginationV2.maxPage           -> how many ?page=N pages exist

NOTE: Walmart's online alcohol assortment is location-gated and modest — a
category like whiskey may report maxPage=1 / ~24 items at an unpinned location.
Pin a store (WalmartSession(store_id=...) / --store) to widen the assortment.
"""

from __future__ import annotations

import logging
import re
from typing import Iterator

from .browser import PXBlocked

log = logging.getLogger("scraper.walmart.browse")

WM = "https://www.walmart.com"
# Alcohol browse categories. The dept root (…_2975985) is the broad one; the
# subcategories surface items the root's pagination may not reach.
ALCOHOL_CATEGORIES = [
    "/browse/food/976759_2975985",                                  # Alcohol (all)
    "/browse/food/all-whiskey/976759_2975985_8439204_1991671",      # whiskey/spirits
    "/browse/beer/domestic-beer/976759_2975985_5110387_2104158",
    "/browse/beer/imported-beer/976759_2975985_5110387_3820716",
    "/browse/beer/flavored-specialty-beverages/976759_2975985_5110387_3339948",
]
_IP_ID = re.compile(r"/ip/[^\"'\s]*?/(\d{6,})")


def _ids_and_maxpage(nd: dict) -> tuple[list[str], int]:
    sr = (nd.get("props", {}).get("pageProps", {})
          .get("initialData", {}).get("searchResult", {})) or {}
    ids: list[str] = []
    for stack in sr.get("itemStacks") or []:
        for it in stack.get("items") or []:
            uid = it.get("usItemId") or it.get("id")
            if uid and str(uid).isdigit():
                ids.append(str(uid))
            else:
                m = _IP_ID.search(it.get("canonicalUrl", "") or "")
                if m:
                    ids.append(m.group(1))
    maxp = ((sr.get("paginationV2") or {}).get("maxPage")) or 1
    return ids, int(maxp)


def iter_alcohol_product_ids(
    sess, *, limit: int | None = None, max_pages: int = 25
) -> Iterator[str]:
    """Yield distinct Walmart usItemIds for alcohol products, walking each
    category page 1..maxPage. Dedupes across categories."""
    seen: set[str] = set()
    count = 0
    for cat in ALCOHOL_CATEGORIES:
        try:
            nd = sess.next_data(f"{WM}{cat}")
        except PXBlocked:
            log.warning("browse blocked: %s", cat)
            continue
        ids, maxp = _ids_and_maxpage(nd)
        pages = min(maxp, max_pages)
        page = 1
        while True:
            for pid in ids:
                if pid in seen:
                    continue
                seen.add(pid)
                yield pid
                count += 1
                if limit is not None and count >= limit:
                    return
            page += 1
            if page > pages:
                break
            try:
                nd = sess.next_data(f"{WM}{cat}?page={page}")
            except PXBlocked:
                break
            ids, _ = _ids_and_maxpage(nd)
            if not ids:
                break
        log.info("category done: %s (maxPage=%d, running total %d ids)", cat, maxp, len(seen))
