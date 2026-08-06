"""Parse a Walmart product page's __NEXT_DATA__ into validated records.

Confirmed shape (recon): the product lives at
  props.pageProps.initialData.data.product
with .usItemId, .name, .brand, .priceInfo.currentPrice.price, .averageRating,
.numberOfReviews, .availabilityStatus; category at
  ...contentLayout.pageMetadata.pageContext.itemContext.categoryPathName
  ("Home Page/Food/Alcohol/<type>/<subtype>").

Review *text* isn't in the product node; it's on the dedicated reviews page
(/reviews/product/<id>) at data.reviews.customerReviews — see parse_reviews().
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .models import ProductIn, ReviewIn, VariantIn

WM = "https://www.walmart.com"
_SIZE_RE = re.compile(
    r"(\d+(?:\.\d+)?\s?(?:ml|l|liter|fl\s?oz|oz|pk|pack|ct|count))", re.I)


def _dig(d: dict, *path):
    cur = d
    for k in path:
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            return None
    return cur


def _category(data: dict) -> tuple[str | None, str | None]:
    """(category, subcategory) from 'Home Page/Food/Alcohol/Type/Subtype'."""
    cpn = _dig(data, "contentLayout", "pageMetadata", "pageContext",
               "itemContext", "categoryPathName")
    if not cpn:
        return None, None
    parts = [p for p in cpn.split("/") if p]
    if "Alcohol" in parts:
        tail = parts[parts.index("Alcohol") + 1:]
    else:
        tail = parts[-2:]
    cat = tail[0] if tail else None
    sub = tail[1] if len(tail) > 1 else None
    return cat, sub


# Walmart shelves non-alcoholic drinks, mixers, kits and accessories under the
# Alcohol department, so being under /Alcohol/ isn't enough — exclude these.
_NON_ALCOHOL_TYPES = {
    "non-alcoholic beverages", "non alcoholic beverages", "mixers", "cocktail mixers",
    "bar & wine accessories", "bar accessories", "wine accessories", "shop by theme",
    "glassware", "drinkware", "gifts", "gift sets",
}
_NON_ALCOHOL_NAME = (
    "non alcoholic", "non-alcoholic", "0.0", "mixer", "cocktail kit", "gift set",
    "glassware", "glasses", "glass set", "stemware", "bottle opener", "corkscrew",
    "decanter", "tumbler", "flask", "coaster", "set of ",
    "cherries", "syrup", "garnish", "olives", "rimming salt", "bitters",
)


def is_alcohol(data: dict) -> bool:
    """True only for actual alcoholic beverages under the Alcohol department
    (excludes the non-alcoholic drinks, mixers, kits and accessories Walmart
    also files there)."""
    cpn = (_dig(data, "contentLayout", "pageMetadata", "pageContext",
                "itemContext", "categoryPathName") or "")
    if "/Alcohol/" not in cpn and not cpn.endswith("/Alcohol"):
        return False
    parts = [p for p in cpn.split("/") if p]
    if "Alcohol" in parts:
        tail = parts[parts.index("Alcohol") + 1:]
        if tail and tail[0].strip().lower() in _NON_ALCOHOL_TYPES:
            return False
    name = (_dig(data, "product", "name") or "").lower()
    if any(m in name for m in _NON_ALCOHOL_NAME):
        return False
    return True


def parse_next_data(nd: dict, *, alcohol_only: bool = True):
    """__NEXT_DATA__ dict -> (ProductIn | None, VariantIn | None)."""
    data = _dig(nd, "props", "pageProps", "initialData", "data")
    if not data:
        return None, None
    if alcohol_only and not is_alcohol(data):
        return None, None
    prod = data.get("product") or {}
    pid = str(prod.get("usItemId") or "").strip()
    name = prod.get("name")
    if not pid or not name:
        return None, None

    cat, sub = _category(data)
    price = _dig(prod, "priceInfo", "currentPrice", "price")
    size_m = _SIZE_RE.search(name or "")

    try:
        product = ProductIn(
            product_id=pid,
            name=name,
            brand=prod.get("brand"),
            category=cat,
            subcategory=sub,
            url=f"{WM}/ip/{pid}",
            avg_rating=prod.get("averageRating") or None,
            review_count=prod.get("numberOfReviews") or None,
        )
    except Exception:
        return None, None

    try:
        variant = VariantIn(
            variant_id=pid,
            product_id=pid,
            size=size_m.group(1) if size_m else None,
            price=float(price) if price is not None else None,
            in_stock=(prod.get("availabilityStatus") == "IN_STOCK"),
        )
    except Exception:
        variant = None
    return product, variant


def _wm_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(str(s), "%m/%d/%Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_reviews(reviews_nd: dict, *, product_id: str) -> list[ReviewIn]:
    """Parse a /reviews/product/<id> page's __NEXT_DATA__ -> ReviewIn list.

    Reviews live at data.reviews.customerReviews (10 per page): reviewId,
    rating, reviewTitle, reviewText, userNickname, reviewSubmissionTime,
    positiveFeedback.
    """
    reviews = _dig(reviews_nd, "props", "pageProps", "initialData", "data",
                   "reviews", "customerReviews") or []
    out: list[ReviewIn] = []
    for r in reviews:
        rid = str(r.get("reviewId") or "").strip()
        if not rid:
            continue
        try:
            out.append(ReviewIn(
                review_id=rid,
                product_id=product_id,
                rating=r.get("rating"),
                title=r.get("reviewTitle"),
                body=r.get("reviewText"),
                author=r.get("userNickname"),
                review_date=_wm_date(r.get("reviewSubmissionTime")),
                helpful_count=r.get("positiveFeedback"),
            ))
        except Exception:
            continue
    return out
