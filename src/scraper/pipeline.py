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
import random
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


class _Throttle:
    """Adaptive pacing that ramps up when PerimeterX blocks and decays slowly.

    Unlike a per-request cooldown that resets on every success, the penalty
    persists across items, so a session that PX has started challenging slows
    down and *stays* slow for a while — which is what actually lowers the bot
    score and breaks the block-every-1-2-items loop.
    """

    def __init__(self, base: float, cap: float = 45.0):
        self.base = base
        self.cap = cap
        self.penalty = 0.0
        self.consec = 0

    def on_block(self) -> float:
        self.consec += 1
        # escalate the penalty; long recovery cooldown scales with the streak
        self.penalty = min(self.cap, max(self.penalty * 1.7, 4.0) + 3.0)
        return min(self.cap, 5.0 * self.consec)

    def on_success(self) -> None:
        self.consec = 0
        self.penalty *= 0.7          # decay slowly, not instantly

    def pace(self) -> None:
        wait = self.base + self.penalty
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.4 * wait + 0.3))  # jitter


def _fetch_with_rewarm(sess, url: str, thr: "_Throttle") -> dict | None:
    """Fetch a URL; on a PX block, back off, re-warm, and retry once."""
    try:
        return sess.fetch(url)
    except PXBlocked:
        pass
    cooldown = thr.on_block()
    log.warning("PX blocked (%d in a row); cooldown %.0fs + re-warm (penalty now %.1fs)",
                thr.consec, cooldown, thr.penalty)
    time.sleep(cooldown)
    sess.rewarm()
    try:
        return sess.fetch(url)
    except PXBlocked:
        return None


def backfill_reviews(*, limit: int | None = None, delay_s: float = 1.0) -> dict:
    """Re-fetch products that have a review count but no stored reviews.

    Early fast runs sometimes returned before the reviews XHR fired; this
    revisits just those products (using each one's stored URL — no re-crawl)
    and captures the reviews the scroll+grace now triggers.
    """
    from sqlalchemy import text
    from .config import config as _cfg

    with session_scope() as session:
        rows = session.execute(text(
            f"SELECT product_id, url FROM {_cfg.db_schema}.product "
            f"WHERE review_count > 0 AND url IS NOT NULL "
            f"AND product_id NOT IN (SELECT DISTINCT product_id FROM {_cfg.db_schema}.review) "
            f"ORDER BY review_count DESC"
            + (f" LIMIT {int(limit)}" if limit else ""))).all()

    targets = [(r[0], r[1]) for r in rows]
    log.info("backfill: %d products missing reviews", len(targets))
    fixed = still_empty = blocked = 0
    thr = _Throttle(base=delay_s)

    with TotalWineSession() as sess:
        for pid, url in targets:
            try:
                data = _fetch_with_rewarm(sess, url, thr)
            except Exception as e:  # noqa: BLE001
                log.warning("backfill error %s: %s", url, e)
                thr.pace()
                continue
            if data is None:
                blocked += 1
                thr.pace()
                continue
            thr.on_success()
            reviews = parse_reviews(data.get("reviews") or {}, product_id=pid)
            if reviews:
                with session_scope() as session:
                    upsert_reviews(session, [r.model_dump() for r in reviews])
                fixed += 1
            else:
                still_empty += 1
            if (fixed + still_empty) % 25 == 0:
                log.info("backfill: fixed=%d still_empty=%d blocked=%d", fixed, still_empty, blocked)
            thr.pace()

    summary = {"fixed": fixed, "still_empty": still_empty, "blocked": blocked}
    log.info("backfill complete: %s", summary)
    return summary


def run(*, limit: int | None = None, max_sitemaps: int | None = None,
        max_wait_ms: int = 10000, delay_s: float = 1.0,
        block_resources: bool = False, resume: bool = True,
        log_every: int = 25) -> dict:
    """Scrape product data for up to `limit` products from the sitemaps.

    Resumable: with resume=True, products already in the DB are skipped, so a
    re-run continues where a crash/stop left off (and retries anything that was
    blocked, since blocked URLs never made it into the DB).

    Defaults are tuned for PerimeterX-friendliness on a long run: a 1s base
    pace (with jitter), resources NOT blocked (real browsers load assets), and
    adaptive throttling that ramps up after blocks. For a quick small test you
    can pass delay_s=0 / block_resources=True to go faster.
    """
    ingested = skipped = errors = blocked = 0
    thr = _Throttle(base=delay_s)
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
            max_wait_ms=max_wait_ms, block_resources=block_resources
        ) as sess:
            for url in iter_product_urls(limit=limit, max_sitemaps=max_sitemaps):
                code = _url_code(url)
                if resume and code and code in done:
                    skipped += 1
                    continue

                try:
                    data = _fetch_with_rewarm(sess, url, thr)
                except Exception as e:  # noqa: BLE001
                    errors += 1
                    log.warning("fetch/parse error %s: %s", url, e)
                    continue
                if data is None:
                    blocked += 1
                    thr.pace()
                    continue
                thr.on_success()

                if _persist_one(data):
                    ingested += 1
                    if code:
                        done.add(code)   # avoid re-fetching within this run too
                else:
                    errors += 1

                if ingested and ingested % log_every == 0:
                    log.info("ingested=%d skipped=%d errors=%d blocked=%d penalty=%.1fs",
                             ingested, skipped, errors, blocked, thr.penalty)
                thr.pace()
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
