"""Parse the getProduct API payload into validated Product + Variant records.

Shape confirmed from a live capture (spike_out/network/resp_004.json):

    {
      "id": 140521750, "skuId": "140521750-1",
      "name": "...", "brand": {"name": "Jim Beam", ...},
      "productUrl": "/spirits/.../p/140521750",
      "department": "c0030",
      "categories": [{"name": "American Whiskey", "type": "PRODUCT_TYPE"},
                     {"name": "Whiskey", "type": "VARIETAL_TYPE"}, ...],
      "price": [{"price": 21.19, "type": "EDLP"}],
      "customerAverageRating": 4.52, "customerReviewsCount": 21,
      "packageDescription": "750ml Bottle",
      "skus": [{"skuId": "140521750-1",
                "options": [{"type": "SIZE", "value": "750ml"}, ...]}],
      "stockLevel": [{"stock": 8, "purchaseLimit": 8}],
      "unavailableAtStore": false,
      "alcoholPercentage": 75.5
    }
"""

from __future__ import annotations

from .config import config
from .models import ProductIn, VariantIn


def _category_of(categories: list[dict], type_: str) -> str | None:
    for c in categories or []:
        if c.get("type") == type_:
            return c.get("name")
    return None


def _size(product: dict) -> str | None:
    for sku in product.get("skus", []):
        for opt in sku.get("options", []):
            if opt.get("type") == "SIZE":
                return opt.get("value")
    return product.get("packageDescription")


def _first_price(product: dict) -> float | None:
    price = product.get("price")
    if isinstance(price, list) and price:
        try:
            return float(price[0].get("price"))
        except (TypeError, ValueError):
            return None
    return None


def _in_stock(product: dict) -> bool | None:
    if product.get("unavailableAtStore") is True:
        return False
    levels = product.get("stockLevel")
    if isinstance(levels, list) and levels:
        try:
            return int(levels[0].get("stock", 0)) > 0
        except (TypeError, ValueError):
            return None
    return None


def parse_product(
    payload: dict, *, ai_review_summary: str | None = None
) -> tuple[ProductIn | None, VariantIn | None]:
    """getProduct JSON -> (ProductIn, VariantIn). Returns (None, None) if invalid."""
    pid = str(payload.get("id") or "").strip()
    sku_id = str(payload.get("skuId") or "").strip()
    if not pid or not sku_id:
        return None, None

    categories = payload.get("categories", [])
    url = payload.get("productUrl")
    if url and not url.startswith("http"):
        url = f"{config.base_url}{url}"

    try:
        product = ProductIn(
            product_id=pid,
            name=payload.get("name", ""),
            brand=(payload.get("brand") or {}).get("name"),
            category=_category_of(categories, "PRODUCT_TYPE") or payload.get("department"),
            subcategory=_category_of(categories, "VARIETAL_TYPE"),
            url=url,
            ai_review_summary=ai_review_summary or None,
            avg_rating=payload.get("customerAverageRating"),
            review_count=payload.get("customerReviewsCount"),
        )
    except Exception:
        return None, None

    try:
        variant = VariantIn(
            variant_id=sku_id,
            product_id=pid,
            size=_size(payload),
            price=_first_price(payload),
            in_stock=_in_stock(payload),
        )
    except Exception:
        variant = None

    return product, variant
