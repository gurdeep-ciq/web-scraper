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
import re
import time

from .browser import PXBlocked, TotalWineSession
from .db import (
    existing_product_ids,
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

_CODE = re.compile(r"/p/(\d+)")


def _url_code(url: str) -> str | None:
    """The product code in a /p/<code> URL == the DB product_id."""
    m = _CODE.search(url)
    return m.group(1) if m else None


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


def _fetch_with_rewarm(sess, url: str, consec_blocks: int) -> tuple[dict | None, int]:
    """Fetch a URL; on a PX block, re-warm the session and retry once.

    Returns (data|None, new_consecutive_block_count). Applies a growing cooldown
    the more consecutive blocks we hit, so a bad patch backs off instead of
    hammering PerimeterX.
    """
    try:
        return sess.fetch(url), 0
    except PXBlocked:
        pass

    consec_blocks += 1
    cooldown = min(60, 5 * consec_blocks)
    log.warning("PX blocked (%d in a row); re-warming, cooldown %ds", consec_blocks, cooldown)
    time.sleep(cooldown)
    sess.rewarm()
    try:
        return sess.fetch(url), 0        # recovered
    except PXBlocked:
        return None, consec_blocks       # still blocked; caller records + moves on


def run(*, limit: int | None = None, max_sitemaps: int | None = None,
        max_wait_ms: int = 10000, delay_s: float = 0.0,
        block_resources: bool = True, resume: bool = True,
        log_every: int = 25) -> dict:
    """Scrape product data for up to `limit` products from the sitemaps.

    Resumable: with resume=True, products already in the DB are skipped, so a
    re-run continues where a crash/stop left off (and retries anything that was
    blocked, since blocked URLs never made it into the DB).
    """
    ingested = skipped = errors = blocked = 0
    consec_blocks = 0
    done = existing_product_ids() if resume else set()
    if done:
        log.info("resume: %d products already in DB will be skipped", len(done))

    with session_scope() as session:
        run_row = ScrapeRun(category="sitemap")
        session.add(run_row)
        session.flush()
        run_id = run_row.id

    try:
        with TotalWineSession(
            max_wait_ms=max_wait_ms, delay_s=delay_s, block_resources=block_resources
        ) as sess:
            for url in iter_product_urls(limit=limit, max_sitemaps=max_sitemaps):
                code = _url_code(url)
                if resume and code and code in done:
                    skipped += 1
                    continue

                try:
                    data, consec_blocks = _fetch_with_rewarm(sess, url, consec_blocks)
                except Exception as e:  # noqa: BLE001
                    errors += 1
                    log.warning("fetch/parse error %s: %s", url, e)
                    continue
                if data is None:
                    blocked += 1
                    continue

                if _persist_one(data):
                    ingested += 1
                    if code:
                        done.add(code)   # avoid re-fetching within this run too
                else:
                    errors += 1

                if ingested and ingested % log_every == 0:
                    log.info("ingested=%d skipped=%d errors=%d blocked=%d",
                             ingested, skipped, errors, blocked)
    finally:
        with session_scope() as session:
            row = session.get(ScrapeRun, run_id)
            row.finished_at = utcnow()
            row.records_ingested = ingested
            row.error_count = errors + blocked
            row.notes = f"skipped={skipped} blocked={blocked}"

    summary = {"ingested": ingested, "skipped": skipped, "errors": errors,
               "blocked": blocked, "run_id": run_id}
    log.info("run complete: %s", summary)
    return summary
