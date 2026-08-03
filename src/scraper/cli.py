"""Command-line entrypoint.

    python -m scraper.cli init-db                 # create schema + tables
    python -m scraper.cli sync-stores             # load store directory (curl)
    python -m scraper.cli run --limit 200         # scrape product data (browser)
    python -m scraper.cli run                      # full catalog (~85k)
"""

from __future__ import annotations

import argparse
import logging


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="scraper")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="create schema + tables")
    sub.add_parser("sync-stores", help="load the store directory from the sitemap")

    p_dash = sub.add_parser("dashboard", help="serve the ingestion dashboard")
    p_dash.add_argument("--port", type=int, default=8000)

    p_run = sub.add_parser("run", help="scrape product data from the sitemaps")
    p_run.add_argument("--limit", type=int, default=None, help="max products")
    p_run.add_argument("--max-sitemaps", type=int, default=None,
                       help="cap number of product sitemaps scanned")
    p_run.add_argument("--max-wait-ms", type=int, default=12000,
                       help="max ms to wait for getProduct before giving up on a page")
    p_run.add_argument("--delay-s", type=float, default=0.0,
                       help="polite pause between products (0 = fastest)")
    p_run.add_argument("--no-block", action="store_true",
                       help="do not block images/css/fonts (slower)")
    p_run.add_argument("--no-resume", action="store_true",
                       help="do not skip products already in the DB")

    p_par = sub.add_parser("run-parallel",
                           help="scrape with N browser workers (needs 1 proxy/IP each)")
    p_par.add_argument("--limit", type=int, default=None, help="max products (total)")
    p_par.add_argument("--workers", type=int, default=1,
                       help="concurrent browsers (use 1 without proxies; PX gates by IP)")
    p_par.add_argument("--proxies", nargs="*", default=None,
                       help="one proxy URL per worker, e.g. http://user:pass@ip:port ...")
    p_par.add_argument("--delay-s", type=float, default=0.0,
                       help="per-worker pause between products")

    args = ap.parse_args()

    if args.cmd == "init-db":
        from .db import init_db

        init_db()
        print("schema + tables created")
    elif args.cmd == "sync-stores":
        from .pipeline import sync_stores

        print({"stores": sync_stores()})
    elif args.cmd == "dashboard":
        from .dashboard import serve

        serve(args.port)
    elif args.cmd == "run":
        from .pipeline import run

        print(run(limit=args.limit, max_sitemaps=args.max_sitemaps,
                  max_wait_ms=args.max_wait_ms, delay_s=args.delay_s,
                  block_resources=not args.no_block, resume=not args.no_resume))
    elif args.cmd == "run-parallel":
        from .parallel import run_parallel

        print(run_parallel(limit=args.limit, workers=args.workers,
                           delay_s=args.delay_s, proxies=args.proxies))


if __name__ == "__main__":
    main()
