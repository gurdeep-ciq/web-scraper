"""Parallel scraping: N browser workers splitting the URL list, all writing to
the same Postgres.

IMPORTANT — PerimeterX gates concurrency by IP. Running multiple workers behind
ONE IP (no proxies) makes PX escalate every worker to a "Press & Hold" challenge
(they share the cloned token/fingerprint). So:

  * WITH --proxies (one IP per worker): near-linear speedup. This is the
    single-box stand-in for the EC2 fleet; each worker solves its own PX on its
    own IP. Use a fresh profile per worker (they can't reuse the local token).
  * WITHOUT proxies: only workers=1 is reliable. More just triggers PX. On a
    fleet, run ONE worker per machine/IP instead.

    python -m scraper.cli run-parallel --limit 500 --proxies http://ip1:port http://ip2:port ...
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import shutil
from pathlib import Path

from .browser import DEFAULT_PROFILE, PXBlocked, TotalWineSession
from .pipeline import _persist_one
from .sitemaps import iter_product_urls

log = logging.getLogger("scraper.parallel")


def _clone_profile(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    for pat in ("Singleton*", "*/Singleton*"):
        for lock in dst.glob(pat):
            try:
                lock.unlink()
            except OSError:
                pass


def _worker(worker_id: int, urls: list[str], profile_dir: str,
            delay_s: float, proxy: str | None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ingested = blocked = errors = 0
    with TotalWineSession(profile_dir=profile_dir, delay_s=delay_s, proxy=proxy) as sess:
        for url in urls:
            try:
                data = sess.fetch(url)
            except PXBlocked:
                blocked += 1
                continue
            except Exception:
                errors += 1
                continue
            if _persist_one(data):
                ingested += 1
            else:
                errors += 1
            if ingested and ingested % 25 == 0:
                log.info("worker %d: ingested=%d blocked=%d", worker_id, ingested, blocked)
    log.info("worker %d done: ingested=%d blocked=%d errors=%d",
             worker_id, ingested, blocked, errors)


def run_parallel(*, limit: int | None = None, workers: int = 1,
                 delay_s: float = 0.0, proxies: list[str] | None = None) -> dict:
    if proxies:
        workers = len(proxies)
    elif workers > 1:
        log.warning(
            "workers=%d with NO proxies: PerimeterX will challenge each worker "
            "(shared IP). Use --proxies, or run workers=1 per machine.", workers
        )

    urls = list(iter_product_urls(limit=limit))
    if not urls:
        return {"workers": workers, "urls": 0}

    shards = [urls[i::workers] for i in range(workers)]
    clone_root = DEFAULT_PROFILE.parent / "px_clones"
    clone_root.mkdir(parents=True, exist_ok=True)

    log.info("launching %d worker(s) over %d urls (~%d each)",
             workers, len(urls), len(shards[0]))

    ctx = mp.get_context("spawn")
    procs = []
    for wid in range(workers):
        clone = clone_root / f"w{wid}"
        _clone_profile(DEFAULT_PROFILE, clone)
        proxy = proxies[wid] if proxies else None
        p = ctx.Process(target=_worker, args=(wid, shards[wid], str(clone), delay_s, proxy))
        p.start()
        procs.append(p)
    for p in procs:
        p.join()

    log.info("parallel run complete (see DB for row counts)")
    return {"workers": workers, "urls": len(urls)}
