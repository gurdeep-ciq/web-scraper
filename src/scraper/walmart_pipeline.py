"""Walmart alcohol pipeline: enumerate alcohol product ids -> fetch each /ip/
page -> parse __NEXT_DATA__ -> validate -> Postgres (source='walmart').

Reuses the shared DB layer, adaptive throttle, resume, and blocked-tracking
from the Total Wine pipeline; only enumeration/session/parsing are Walmart-specific.
"""

from __future__ import annotations

import logging
import time

from .browser import PXBlocked
from .db import (
    clear_blocked,
    excluded_product_ids,
    existing_blocked_ids,
    existing_product_ids,
    insert_variants,
    record_blocked,
    session_scope,
    upsert_products,
    upsert_reviews,
)
from .models import ScrapeRun, utcnow
from .pipeline import _Throttle, _stamp
from .walmart_browse import WM, iter_alcohol_product_ids
from .walmart_parse import is_alcohol, parse_next_data, parse_reviews
from .walmart_session import WalmartSession

log = logging.getLogger("scraper.walmart")
SOURCE = "walmart"


def _fetch(sess: WalmartSession, pid: str, thr: _Throttle) -> dict | None:
    """Fetch a product's __NEXT_DATA__; on a PX block, cooldown + re-warm + retry."""
    url = f"{WM}/ip/{pid}"
    try:
        return sess.next_data(url)
    except PXBlocked:
        pass
    cooldown = thr.on_block()
    log.info("transient PX block (%d in a row); %ds cooldown + re-warm, then retry",
             thr.consec, int(cooldown))
    time.sleep(cooldown)
    sess.rewarm()
    try:
        return sess.next_data(url)
    except PXBlocked:
        return None


def run_walmart(*, limit: int | None = None, delay_s: float = 1.0, resume: bool = True,
                pause_every: int = 0, pause_seconds: float = 180.0, warm_seconds: int = 60,
                retry_blocked: bool = False, store_id: str | None = None,
                max_pages: int = 25, log_every: int = 25) -> dict:
    ingested = skipped = errors = blocked = nonalcohol = 0
    thr = _Throttle(base=delay_s)
    done = existing_product_ids(SOURCE) if resume else set()
    blocked_ids = set() if retry_blocked else existing_blocked_ids(SOURCE)
    # Non-alcohol products are remembered and ALWAYS skipped (never re-fetched),
    # so we don't burn a page load re-discovering they're not alcohol each run.
    excluded_ids = excluded_product_ids(SOURCE)
    if done:
        log.info("resume: %d walmart products already in DB will be skipped", len(done))
    if blocked_ids:
        log.info("skipping %d previously-blocked (use --retry-blocked)", len(blocked_ids))
    if excluded_ids:
        log.info("skipping %d known non-alcohol products", len(excluded_ids))

    with session_scope() as session:
        row = ScrapeRun(source=SOURCE, category="alcohol")
        session.add(row)
        session.flush()
        run_id = row.id

    try:
        with WalmartSession(delay_s=0.0, store_id=store_id) as sess:
            log.info("warming up on walmart.com (solve a Press & Hold if shown, up to %ds)...",
                     warm_seconds)
            if not sess.warm_up(max_seconds=warm_seconds):
                log.warning("warm-up still blocked; the IP may be hot — continuing")

            for pid in iter_alcohol_product_ids(sess, max_pages=max_pages):
                if pid in done or pid in blocked_ids or pid in excluded_ids:
                    skipped += 1
                    continue
                try:
                    nd = _fetch(sess, pid, thr)
                except Exception as e:  # noqa: BLE001
                    errors += 1
                    log.warning("fetch error /ip/%s: %s", pid, e)
                    continue
                if nd is None:
                    blocked += 1
                    with session_scope() as session:
                        record_blocked(session, SOURCE, pid, f"{WM}/ip/{pid}")
                    thr.pace()
                    continue
                thr.on_success()

                # Parse without the filter so we can tell "not alcohol" (remember
                # + skip forever) apart from "unparseable" (a real error).
                product, variant = parse_next_data(nd, alcohol_only=False,
                                                   store_id=store_id or "")
                data = (nd.get("props", {}).get("pageProps", {})
                        .get("initialData", {}).get("data", {}))
                if product is None:
                    errors += 1
                    thr.pace()
                    continue
                if not is_alcohol(data):
                    nonalcohol += 1
                    excluded_ids.add(pid)
                    with session_scope() as session:
                        record_blocked(session, SOURCE, pid, f"{WM}/ip/{pid}",
                                       reason="nonalcohol")
                    thr.pace()
                    continue

                with session_scope() as session:
                    upsert_products(session, _stamp([product.model_dump()], SOURCE))
                    if variant is not None:
                        insert_variants(session, _stamp([variant.model_dump()], SOURCE))
                    if retry_blocked:
                        clear_blocked(session, SOURCE, pid)
                ingested += 1
                done.add(pid)

                # Reviews live on a separate page; fetch it only if the product
                # actually has reviews. A block here doesn't fail the product.
                if product.review_count:
                    try:
                        rvs = parse_reviews(sess.reviews(pid), product_id=pid)
                    except PXBlocked:
                        rvs = []
                    if rvs:
                        with session_scope() as session:
                            upsert_reviews(session, _stamp([r.model_dump() for r in rvs], SOURCE))
                    thr.pace()

                if ingested % log_every == 0:
                    log.info("ingested=%d skipped=%d nonalcohol=%d blocked=%d penalty=%.1fs",
                             ingested, skipped, nonalcohol, blocked, thr.penalty)
                if pause_every and ingested % pause_every == 0:
                    log.info("patient pause: %.0fs after %d products", pause_seconds, ingested)
                    time.sleep(pause_seconds)
                thr.pace()
                if limit and ingested >= limit:
                    break
    finally:
        with session_scope() as session:
            row = session.get(ScrapeRun, run_id)
            row.finished_at = utcnow()
            row.records_ingested = ingested
            row.error_count = errors + blocked
            row.notes = f"skipped={skipped} nonalcohol={nonalcohol} blocked={blocked}"

    summary = {"ingested": ingested, "skipped": skipped, "nonalcohol": nonalcohol,
               "blocked": blocked, "errors": errors, "run_id": run_id}
    log.info("walmart run complete: %s", summary)
    return summary
