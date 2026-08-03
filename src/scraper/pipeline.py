"""Pipeline orchestration.

    sync_stores()  -> curl the store sitemap -> upsert `store`
    run(limit)     -> enumerate product URLs from sitemaps
                      -> warm browser session fetches each product page
                      -> intercept getProduct / reviews / summary
                      -> validate (Pydantic) -> upsert Postgres
                      -> track progress in `scrape_run`

Each product is committed as it's scraped, so a mid-run crash keeps everything
gathered so far and `scrape_run` reflects real progress.
"""

from __future__ import annotations

import logging

from .browser import PXBlocked, TotalWineSession
from .db import (
    insert_variants,
    session_scope,
    upsert_products,
    upsert_reviews,
    upsert_stores,
)
from .models import ScrapeRun, utcnow
from .products import parse_product
from .reviews import parse_reviews, parse_summary
from .sitemaps import iter_product_urls, iter_stores

log = logging.getLogger("scraper.pipeline")


def sync_stores() -> int:
    """Load the store directory from the sitemap (no browser needed)."""
    stores = [s.model_dump() for s in iter_stores()]
    with session_scope() as session:
        n = upsert_stores(session, stores)
    log.info("synced %d stores", n)
    return n


def _persist_one(data: dict) -> bool:
    """Validate + persist one product's payloads. Returns True on success."""
    product_json = data.get("product")
    if not product_json:
        return False
    summary = parse_summary(data.get("summary") or {})
    product, variant = parse_product(product_json, ai_review_summary=summary)
    if product is None:
        return False

    pid = product.product_id
    reviews = parse_reviews(data.get("reviews") or {}, product_id=pid)

    with session_scope() as session:
        upsert_products(session, [product.model_dump()])
        if variant is not None:
            insert_variants(session, [variant.model_dump()])
        if reviews:
            upsert_reviews(session, [r.model_dump() for r in reviews])
    return True


def run(*, limit: int | None = None, max_sitemaps: int | None = None,
        settle_ms: int = 6000, log_every: int = 25) -> dict:
    """Scrape product data for up to `limit` products from the sitemaps."""
    ingested = 0
    errors = 0
    blocked = 0

    with session_scope() as session:
        run_row = ScrapeRun(category="sitemap")
        session.add(run_row)
        session.flush()
        run_id = run_row.id

    try:
        with TotalWineSession(settle_ms=settle_ms) as sess:
            for url in iter_product_urls(limit=limit, max_sitemaps=max_sitemaps):
                try:
                    data = sess.fetch(url)
                except PXBlocked:
                    blocked += 1
                    log.warning("PX blocked: %s", url)
                    continue
                except Exception as e:  # noqa: BLE001
                    errors += 1
                    log.warning("fetch/parse error %s: %s", url, e)
                    continue

                if _persist_one(data):
                    ingested += 1
                else:
                    errors += 1

                if ingested and ingested % log_every == 0:
                    log.info("ingested=%d errors=%d blocked=%d", ingested, errors, blocked)
    finally:
        with session_scope() as session:
            row = session.get(ScrapeRun, run_id)
            row.finished_at = utcnow()
            row.records_ingested = ingested
            row.error_count = errors + blocked
            row.notes = f"blocked={blocked}"

    summary = {"ingested": ingested, "errors": errors, "blocked": blocked, "run_id": run_id}
    log.info("run complete: %s", summary)
    return summary
